import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.orchestration.contracts import ResearchContract
from app.orchestration.passages import find_passage, visible
from app.research import live_source, source_domain


class Evidence(BaseModel):
    source_url: str
    quote: str = Field(min_length=1)


class RequirementCheck(BaseModel):
    requirement: str
    requirement_id: str = ""
    status: Literal["met", "not_met", "unknown"]
    statement: str
    evidence: list[Evidence] = Field(description="Required. For met/not_met, provide the exact source_url and a verbatim quote proving this requirement for this finding. Use [] only for unknown.")


class Finding(BaseModel):
    name: str
    description: str = Field(default="", description="One concise factual description of this option, supported by description_evidence.")
    description_evidence: list[Evidence] = Field(default_factory=list, max_length=4)
    assessment: str = Field(default="", description="Brief practical judgment about why to consider this option; do not introduce unsupported facts.")
    assessment_evidence: list[Evidence] = Field(default_factory=list)
    source_url: str | None = Field(default=None, description="Exact supplied URL establishing the identity of this candidate, even when its requirements are unknown.")
    source_kind: Literal["product", "manufacturer", "review", "comparison", "other"] = "other"
    checks: list[RequirementCheck] = Field(min_length=1)


class GroundedAnswer(BaseModel):
    response: str = Field(default="", description="Conversational answer for explanations or research questions that do not call for comparing candidates. Cite supplied URLs for current facts. Leave empty when findings are used.")
    requested_count: int | None = Field(default=None, ge=1, le=100, description="Number of items explicitly requested by the user, or null.")
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def bounded_findings(cls, findings: list[Finding]) -> list[Finding]:
        # Enforce this locally: maxItems on the outer nested schema exceeds Gemini's schema budget.
        if len(findings) > 30:
            raise ValueError("Return at most 30 findings")
        return findings


def plain(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def passage_present(quote: str, text: str) -> bool:
    return find_passage(quote, text) is not None


def matching_evidence(evidence: list[Evidence], sources: list[dict]) -> list[Evidence]:
    texts = {source["url"]: source["text"] for source in sources}
    return [item for item in evidence if item.source_url in texts
            and passage_present(item.quote, texts[item.source_url])]


def apply_contract(answer: GroundedAnswer, sources: list[dict], contract: ResearchContract | None) -> GroundedAnswer:
    if not contract:
        return answer
    answer = answer.model_copy(deep=True)
    answer.requested_count = contract.requested_count
    if contract.shopping and not answer.findings:
        answer.response = ""
    by_url = {source["url"]: source for source in sources}
    for finding in answer.findings:
        if contract.shopping:
            # Shopping facts are rendered from the same checked fields used for eligibility.
            finding.description = finding.assessment = ""
            finding.description_evidence = finding.assessment_evidence = []
        checks = {check.requirement_id: check for check in finding.checks}
        required = []
        for requirement in contract.requirements:
            check = checks.get(requirement.id)
            if check is None:
                check = RequirementCheck(requirement_id=requirement.id, requirement=requirement.description,
                                         status="unknown", statement="", evidence=[])
            check.requirement = requirement.description
            if contract.shopping and requirement.kind in {"price", "stock", "delivery"}:
                product = by_url.get(finding.source_url, {})
                evidence = matching_evidence(check.evidence, sources)
                if requirement.kind in {"price", "stock"}:
                    evidence = [e for e in evidence if e.source_url == finding.source_url
                                and live_source(product)]
                else:
                    evidence = [e for e in evidence if source_domain(e.source_url) == source_domain(finding.source_url or "")
                                and by_url[e.source_url].get("retrieval") in {"live", "page"}]
                # Reviews/comparison pages and search snippets cannot establish a purchasable offer.
                if finding.source_kind != "product" or not evidence:
                    check.status, check.statement, check.evidence = "unknown", "", []
                else:
                    check.evidence = evidence
            required.append(check)
        finding.checks = required or finding.checks
    if contract.shopping:
        answer.findings = [finding for finding in answer.findings if finding.source_kind == "product"]
        answer.findings.sort(key=lambda finding: (
            any(c.status == "not_met" for c in finding.checks),
            -sum(c.status == "met" and bool(matching_evidence(c.evidence, sources)) for c in finding.checks),
        ))
        # Keep verified options first, spreading equally supported candidates across sellers.
        ranked = []
        for complete in (True, False):
            groups = {}
            for finding in answer.findings:
                if finding_coverage(finding, sources)[1] == complete:
                    groups.setdefault(source_domain(finding.source_url or ""), []).append(finding)
            while any(groups.values()):
                for group in groups.values():
                    if group:
                        ranked.append(group.pop(0))
        answer.findings = ranked
        if contract.requested_count:
            answer.findings = answer.findings[:contract.requested_count]
        domains = {source_domain(f.source_url or "") for f in answer.findings if f.source_kind == "product" and f.source_url}
        if (contract.requested_count or len(answer.findings)) > 1 and len(domains) < 2:
            limitation = "Offers from multiple retailers have not yet been verified."
            if limitation not in answer.limitations:
                answer.limitations.append(limitation)
    return answer


def finding_coverage(finding: Finding, sources: list[dict]) -> tuple[bool, bool]:
    supported_checks = [check.status for check in finding.checks
                        if check.status != "unknown" and matching_evidence(check.evidence, sources)]
    identity = any(source["url"] == finding.source_url and visible(finding.name)
                   and passage_present(finding.name, source.get("title", "") + " " + source["text"])
                   for source in sources)
    retained = bool(identity or supported_checks or matching_evidence(finding.description_evidence, sources))
    complete = bool(finding.checks and len(supported_checks) == len(finding.checks)
                    and all(status == "met" for status in supported_checks))
    return retained, complete


def answer_coverage(answer: GroundedAnswer, sources: list[dict]) -> dict:
    coverage = [finding_coverage(finding, sources) for finding in answer.findings]
    return {"retained": sum(retained for retained, _ in coverage),
            "verified": sum(complete for _, complete in coverage),
            "requested": answer.requested_count,
            "offer_domains": len({source_domain(f.source_url) for f in answer.findings if f.source_kind == "product" and f.source_url}),
            "supported_checks": sum(check.status == "met" and bool(matching_evidence(check.evidence, sources)) for f in answer.findings for check in f.checks),
            "gaps": [{"name": f.name, "source_url": f.source_url, "source_kind": f.source_kind,
                      "requirements": [c.requirement for c in f.checks if c.status != "met" or not matching_evidence(c.evidence, sources)]}
                     for f in answer.findings if not finding_coverage(f, sources)[1]]}


def links(evidence: list[Evidence]) -> str:
    urls = dict.fromkeys(item.source_url for item in evidence)
    return " ".join("[Source](<" + url.replace("(", "%28").replace(")", "%29") + ">)" for url in urls)


def render_answer(answer: GroundedAnswer, sources: list[dict]) -> tuple[str, list[str]]:
    sections = []
    issues = []
    retained = 0
    for finding in answer.findings:
        rows = []
        description_evidence = matching_evidence(finding.description_evidence, sources)
        if finding.description and description_evidence:
            rows.append(plain(finding.description) + " " + links(description_evidence))
        elif finding.description:
            issues.append(f"{finding.name}: no matching evidence for description")
        for check in finding.checks:
            evidence = matching_evidence(check.evidence, sources)
            if check.status == "unknown" or not evidence:
                rows.append(f"- {plain(check.requirement)}: **Unverified**.")
                if check.status != "unknown":
                    issues.append(f"{finding.name}: no matching evidence for {check.requirement}")
                continue
            label = "" if check.status == "met" else "**Does not meet requirement**. "
            rows.append(f"- {plain(check.requirement)}: {label}{plain(check.statement)} {links(evidence)}")
        keep, complete = finding_coverage(finding, sources)
        if keep:
            retained += 1
            assessment_evidence = matching_evidence(finding.assessment_evidence, sources)
            if finding.assessment and assessment_evidence:
                rows.append("Assessment: " + plain(finding.assessment) + " " + links(assessment_evidence))
            if finding.source_url and any(source["url"] == finding.source_url for source in sources):
                rows.append(links([Evidence(source_url=finding.source_url, quote=finding.name)]))
            label = "" if complete else " — option to consider"
            if any(check.status == "not_met" and matching_evidence(check.evidence, sources) for check in finding.checks):
                label = " — does not meet all requirements"
            sections.append(f"### {retained}. {plain(finding.name)}{label}\n\n" + "\n\n".join(rows))
        else:
            issues.append(f"{finding.name}: omitted because no checks had matching evidence")
        if answer.requested_count and retained >= answer.requested_count:
            break
    if not sections:
        if answer.response and not answer.findings:
            sections.append(answer.response)
        elif sources:
            sections.append("I couldn't verify a matching shortlist yet. Here are retrieved pages to check; these are leads, with price, availability and suitability still to confirm.")
            for source in sources[:answer.requested_count or 5]:
                url = source["url"].replace("(", "%28").replace(")", "%29")
                sections.append(f"- [{plain(source.get('title') or source['url'])}](<{url}>)")
            issues.append("No validated findings; showing retrieved leads instead")
        else:
            sections.append("I couldn't retrieve usable pages for this request. Try a broader search or share a listing to check.")
    limitations = list(answer.limitations) if not issues else []
    if issues:
        limitations.append("I could not confirm every detail from the retrieved pages. Unverified details are not confirmed matches.")
    if answer.requested_count and retained and retained < answer.requested_count:
        limitations.append(f"Found {retained} sourced options of the {answer.requested_count} requested.")
    if limitations:
        sections.append("Evidence limits:\n\n" + "\n".join(f"- {plain(item)}" for item in dict.fromkeys(limitations)))
    return "\n\n".join(sections), issues
