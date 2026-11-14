import re
from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source_url: str
    quote: str = Field(min_length=1)


class RequirementCheck(BaseModel):
    requirement: str
    status: Literal["met", "not_met", "unknown"]
    statement: str
    evidence: list[Evidence] = Field(default_factory=list)


class Finding(BaseModel):
    name: str
    checks: list[RequirementCheck] = Field(min_length=1)


class GroundedAnswer(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list)


def plain(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def render_answer(answer: GroundedAnswer, sources: list[dict]) -> tuple[str, list[str]]:
    source_text = {source["url"]: " ".join(source["text"].split()) for source in sources}
    sections = []
    issues = []
    for finding in answer.findings:
        rows = []
        supported = False
        for check in finding.checks:
            evidence = [
                item for item in check.evidence
                if item.source_url in source_text
                and " ".join(item.quote.split()) in source_text[item.source_url]
                and item.quote.strip()
            ]
            if check.status == "unknown" or not evidence:
                rows.append(f"- {plain(check.requirement)}: **Unverified**.")
                if check.status != "unknown":
                    issues.append(f"{finding.name}: no matching evidence for {check.requirement}")
                continue
            supported = True
            label = "Supported" if check.status == "met" else "Does not meet requirement"
            rows.append(f"- {plain(check.requirement)}: **{label}**. {plain(check.statement)}")
            for item in evidence:
                url = item.source_url.replace("(", "%28").replace(")", "%29")
                rows.append(f"  > {plain(item.quote)} [Source](<{url}>)")
        if supported:
            sections.append(f"### {plain(finding.name)}\n\n" + "\n\n".join(rows))
        else:
            issues.append(f"{finding.name}: omitted because no checks had matching evidence")
    if not sections:
        sections.append("The retrieved pages did not provide enough matching evidence to support a finding.")
    else:
        sections.insert(0, "Findings from the retrieved pages. Unverified requirements remain open.")
    limitations = list(answer.limitations)
    if issues:
        limitations.append("Some claims were omitted or marked unverified because their quoted evidence did not match the retrieved pages.")
    if limitations:
        sections.append("### Limitations\n\n" + "\n".join(f"- {plain(item)}" for item in limitations))
    return "\n\n".join(sections), issues
