import json
import re
from unicodedata import combining, normalize

from app.orchestration.contracts import ResearchContract
from app.orchestration.grounding import (
    GroundedAnswer, answer_coverage, apply_contract, finding_coverage,
    links, matching_evidence, monetary_values, labelled_current_price, plain, render_answer,
)
from app.research import live_source, source_domain


def candidate_key(finding, contract):
    name = " ".join(finding.name.casefold().split())
    return (name, finding.source_url if contract.shopping else None)


def same_product_offer(left, right):
    if left.source_kind != "product" or right.source_kind != "product" or not left.source_url or left.source_url != right.source_url:
        return False

    def identity(name):
        name = name.casefold()
        capacities = set(re.sub(r"\s", "", value) for value in re.findall(r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", name))
        screens = set(re.findall(r"\b\d+(?:[.,]\d+)?\s*(?:[\"″]|inch\b)", name))
        processors = set(re.findall(r"\b(?:i[3579]|ryzen\s+[3579]|ultra\s+[3579])\b", name))
        name = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", "", name)
        name = re.sub(r"\bwindows\s*\d+\b", "", name)
        codes = {token for token in re.findall(r"[\w]+(?:-[\w]+)*", name)
                 if len(token) >= 5 and re.search(r"\d", token) and re.search(r"[a-z]", token)}
        return codes, (capacities, screens, processors)

    left_codes, left_config = identity(left.name)
    right_codes, right_config = identity(right.name)
    return bool(left_codes and left_codes == right_codes and left_config == right_config)


def matching_candidate(finding, candidates, contract):
    exact = candidate_key(finding, contract)
    if exact in candidates:
        return exact
    if contract.shopping:
        matches = [key for key, candidate in candidates.items() if same_product_offer(finding, candidate)]
        if len(matches) == 1:
            return matches[0]
    return None


def related_entities(finding, sources):
    return {" ".join(check.related_entity.casefold().split()) for check in finding.checks
            if check.scope == "related" and check.related_entity and check.status == "met"
            and matching_evidence(check.evidence, sources)}


def conflicting_related_checks(finding, sources):
    entities = related_entities(finding, sources)
    if len(entities) < 2:
        return 0
    conflicts = 0
    for check in finding.checks:
        if check.scope == "related" and check.status == "met":
            check.status, check.statement = "unknown", ""
            check.reason = "Conflicting related entities: verify all related requirements for the same option."
            conflicts += 1
    return conflicts


def merge_answers(previous: GroundedAnswer, current: GroundedAnswer, sources: list[dict], contract: ResearchContract):
    previous = apply_contract(previous, sources, contract)
    current = apply_contract(current, sources, contract)
    merged = current.model_copy(deep=True)
    merged.alignment_rejections = list({json.dumps(item, sort_keys=True): item
        for item in [*previous.alignment_rejections, *current.alignment_rejections]}.values())
    if previous.knowledge and not current.findings and not current.response:
        if not merged.knowledge:
            merged.knowledge = previous.knowledge.model_copy(deep=True)
        else:
            known = {point.statement for point in merged.knowledge.points}
            merged.knowledge.points.extend(point.model_copy(deep=True) for point in previous.knowledge.points
                if point.statement not in known and matching_evidence(point.evidence, sources))
            for field in ("verdict", "steps", "draft", "caveats"):
                if not getattr(merged.knowledge, field):
                    setattr(merged.knowledge, field, getattr(previous.knowledge, field))
    by_candidate, duplicates = {}, []
    for finding in current.findings:
        if matching_candidate(finding, by_candidate, contract) is None:
            by_candidate[candidate_key(finding, contract)] = finding
        else:
            duplicates.append(finding)
    current_related = {key: related_entities(finding, sources) for key, finding in by_candidate.items()}
    carried = conflicts = 0
    requirements = {requirement.id: requirement for requirement in contract.requirements}
    by_url = {source["url"]: source for source in sources}
    for old in [*duplicates, *previous.findings]:
        if not finding_coverage(old, sources)[0]:
            continue
        key = matching_candidate(old, by_candidate, contract)
        candidate = by_candidate.get(key)
        if candidate is None:
            by_candidate[candidate_key(old, contract)] = old.model_copy(deep=True)
            carried += sum(c.status != "unknown" and bool(matching_evidence(c.evidence, sources)) for c in old.checks)
            continue
        if contract.shopping and old.name.casefold() in candidate.name.casefold():
            candidate.name = old.name
        checks = {check.requirement_id: check for check in candidate.checks}
        for old_check in old.checks:
            evidence = matching_evidence(old_check.evidence, sources)
            new = checks.get(old_check.requirement_id)
            requirement = requirements.get(old_check.requirement_id)
            if requirement and requirement.kind in {"price", "stock", "availability"} and old_check.basis != 'reported':
                evidence = [item for item in evidence if live_source(by_url[item.source_url])]
            if old_check.status == "unknown" and "conflict" in old_check.reason.casefold() and len(evidence) > 1:
                if new is not None and new.related_entity == old_check.related_entity:
                    new.evidence = list({(e.source_url, e.quote, e.passage_id): e for e in [*evidence, *new.evidence]}.values())
                    new.status, new.statement, new.reason = "unknown", "", old_check.reason
                continue
            if (old_check.status == "unknown" and old_check.basis != 'reported') or not evidence:
                continue
            if old_check.scope == "related" and old_check.related_entity:
                old_entity = " ".join(old_check.related_entity.casefold().split())
                if any(entity != old_entity for entity in current_related.get(key, set())):
                    continue
            if new is None or new.status == "unknown" and new.basis != 'reported' or not matching_evidence(new.evidence, sources):
                if new and "conflict" in new.reason.casefold():
                    continue
                checks[old_check.requirement_id] = old_check.model_copy(deep=True)
                carried += old_check.status != 'unknown'
            elif new.related_entity == old_check.related_entity:
                different_prices = False
                for currency in ("EUR", "USD", "GBP"):
                    before = labelled_current_price(old_check.statement, currency) or monetary_values(old_check.statement, currency)
                    after = labelled_current_price(new.statement, currency) or monetary_values(new.statement, currency)
                    different_prices |= bool(before and after and before != after)
                if old_check.status != new.status or different_prices:
                    new.evidence = list({(e.source_url, e.quote, e.passage_id): e for e in [*old_check.evidence, *new.evidence]}.values())
                    new.status, new.statement = "unknown", ""
                    new.basis = 'conflicting'
                    new.reason = "Conflicting evidence remains; confirm the same configuration, date and price basis."
                    conflicts += 1
        candidate.checks = list(checks.values())
    merged.findings = list(by_candidate.values())
    conflicts += sum(conflicting_related_checks(finding, sources) for finding in merged.findings)
    merged.findings.sort(key=lambda f: (
        any(c.status == "not_met" and c.strength == "hard" for c in f.checks),
        -sum(c.status == "met" and bool(matching_evidence(c.evidence, sources)) for c in f.checks),
        -sum(c.basis == 'reported' and bool(matching_evidence(c.evidence, sources)) for c in f.checks),
    ))
    if contract.requested_count:
        merged.findings = merged.findings[:contract.requested_count]
    return apply_contract(merged, sources, contract), {"carried_checks": carried, "conflicts": conflicts}


def missing_preference_ids(answer: GroundedAnswer, sources: list[dict] | None, contract: ResearchContract) -> set[str]:
    preferences = {requirement.id for requirement in contract.requirements if requirement.strength == "preference"}
    supported = [{check.requirement_id for check in finding.checks
                  if (check.status != "unknown" or check.basis == "reported") and (matching_evidence(check.evidence, sources)
                                                    if sources is not None else check.evidence)}
                 for finding in answer.findings]
    return {identifier for identifier in preferences if not supported or any(identifier not in known for known in supported)}


def evidence_gaps(answer: GroundedAnswer, sources: list[dict], contract: ResearchContract | None = None) -> list[dict]:
    gaps = []
    missing_preferences = missing_preference_ids(answer, sources, contract) if contract else set()
    for finding in answer.findings:
        for check in finding.checks:
            if getattr(check, "refinement_excluded", False):
                continue
            if check.strength == "preference" and (check.status != "unknown" or check.basis == "reported") and matching_evidence(check.evidence, sources):
                continue
            if (check.strength != "hard" and check.requirement_id not in missing_preferences) or (check.status == "met" and matching_evidence(check.evidence, sources)):
                continue
            gaps.append({"candidate": finding.name, "source_url": finding.source_url,
                         "related_entity": check.related_entity, "requirement_id": check.requirement_id,
                         "requirement": check.requirement, "strength": check.strength,
                         "status": check.status, "reason": check.reason})
    return gaps


def replacement_gap_queries(answer: dict, contract: ResearchContract, attempted: set[str], sources=None):
    from app.orchestration.contracts import SearchQuery
    from app.orchestration.interpretation import query_key

    parsed = GroundedAnswer.model_validate(answer)
    queries = []
    for finding in parsed.findings:
        failed = [check for check in finding.checks if check.status == "not_met" and check.strength == "hard"
                  and (matching_evidence(check.evidence, sources) if sources is not None else check.evidence)]
        for check in failed:
            if check.scope == "related":
                subject = "hotel" if re.search(r"hotel", check.requirement, re.I) else "alternative provider"
                text = f"{subject} in {finding.name} official site"
                rejected = check.related_entity or "the rejected related option"
            else:
                subject = contract.subject or "alternative"
                constraints = "; ".join(r.description for r in contract.requirements
                                        if r.strength == "hard" and r.scope == "primary"
                                        and r.kind not in {"stock", "delivery"})
                text = f"{subject} {contract.destination or ''} alternative retailer official offers {constraints}"
                rejected = finding.name
            objective = (f"Find a replacement for {rejected}: {check.requirement}. "
                         f"Use a different sourced option; do not collect more facts for the failed option. "
                         f"Original task: {contract.original_request}")[:2000]
            # A second exact source strategy is finite, so a failed replacement cannot loop forever.
            for suffix in ("", " published location contact booking"):
                query = SearchQuery(query=" ".join((text + suffix).split())[:500], objective=objective,
                                    gap=f"Replacement for {finding.name}: {check.requirement}"[:1000])
                if query_key(query) not in attempted:
                    queries.append(query)
                    break
    return list({query_key(query): query for query in queries}.values())[:4]


def fallback_gap_queries(answer: dict, contract: ResearchContract, attempted: set[str], sources: list[dict] | None = None):
    from app.orchestration.contracts import SearchQuery
    from app.orchestration.interpretation import query_key

    queries = replacement_gap_queries(answer, contract, attempted, sources)
    parsed = GroundedAnswer.model_validate(answer)
    findings = parsed.findings
    missing_preferences = missing_preference_ids(parsed, sources, contract)
    rejected = [finding for finding in findings if any(check.status == "not_met" and check.strength == "hard"
                                                     and check.scope == "primary" and check.evidence
                                                     for check in finding.checks)]
    remaining = [finding for finding in findings if finding not in rejected]
    qualifying_count = sum(all(getattr(check, "refinement_excluded", False) or check.status == "met"
        and (matching_evidence(check.evidence, sources) if sources is not None else check.evidence)
        for check in finding.checks if check.strength == "hard") for finding in remaining)
    missing_count = bool(contract.requested_count and qualifying_count < contract.requested_count)

    def add_query(text, objective, gap):
        query = SearchQuery(query=" ".join(text.split())[:500], objective=objective[:2000], gap=gap[:1000])
        if len(queries) < 4 and query_key(query) not in attempted and all(query_key(query) != query_key(old) for old in queries):
            queries.append(query)

    if (rejected or missing_count) and not queries:
        constraints = "; ".join(requirement.description for requirement in contract.requirements
                                if requirement.strength == "hard" and requirement.scope == "primary")
        gap = "Find replacement candidates satisfying the hard requirements." if rejected else "Find additional distinct candidates to complete the requested shortlist."
        existing = "; ".join(f.name for f in findings)
        sellers = sorted({source_domain(f.source_url) for f in findings if f.source_url}) if contract.shopping else []
        diversity = " from retailers other than " + ", ".join(sellers) if sellers else ""
        add_query(f"{contract.subject or 'Alternatives'} {contract.destination or ''}{diversity}: {constraints}",
                  f"{gap} Requested count: {contract.requested_count or 'unspecified'}. "
                  f"Fully supported candidates: {qualifying_count}. "
                  f"Already discovered: {existing}. Seek new alternatives, not duplicate listings of these candidates. "
                  f"Original task: {contract.original_request}", gap)
        if not queries and contract.shopping and remaining:
            critical = {requirement.id for requirement in contract.requirements
                        if requirement.kind in {"price", "stock", "availability", "delivery"}
                        and requirement.strength == "hard"}
            blocked = [finding for finding in remaining if any(check.requirement_id in critical
                       and (check.status != "met" or not check.evidence) for check in finding.checks)]
            attempted_searches = []
            restricted_sources = False
            for key in attempted:
                try:
                    record = json.loads(key)
                except (ValueError, TypeError):
                    record = {}
                attempted_searches.append(record.get("query", key) if isinstance(record, dict) else key)
                restricted_sources |= bool(isinstance(record, dict) and record.get("include_domains"))
            attempted_text = " ".join(" ".join(query.casefold().split()) for query in attempted_searches)
            searched_each = all(" ".join(finding.name.casefold().split()) in attempted_text for finding in blocked)
            restricted_sources = restricted_sources or bool(re.search(
                r"\b(?:only|exclusively)\s+(?:from|on|at|use|using)\b|\bsite:", contract.original_request, re.I))
            if len(blocked) == len(remaining) and searched_each and not restricted_sources:
                sellers = ", ".join(sorted({source_domain(finding.source_url) for finding in blocked if finding.source_url}))
                seller_preference = f" from retailers other than {sellers}" if sellers else " from different retailers"
                add_query(f"{contract.subject or 'Products'} {contract.destination or ''} explicit in-stock offers{seller_preference}: {constraints}",
                          f"Find alternative sellers or new products with verifiable current prices and stock. "
                          f"Existing tentative sellers: {sellers}. Their exact offers have already been searched without resolving "
                          f"critical purchase facts. Prefer other sellers; keep the tentative options and preserve all hard constraints. "
                          f"Original task: {contract.original_request}", "Find alternative sellers with verifiable purchase offers.")

    requirements = {requirement.id: requirement for requirement in contract.requirements}
    per_candidate = []
    for finding in remaining:
        groups = {}
        for check in finding.checks:
            if getattr(check, "refinement_excluded", False):
                continue
            missing_preference = check.requirement_id in missing_preferences and not (
                (check.status != "unknown" or check.basis == "reported")
                and (matching_evidence(check.evidence, sources) if sources is not None else check.evidence))
            if not missing_preference and (check.strength != "hard" or check.status == "met"):
                continue
            if not missing_preference and check.status == "not_met" and not (check.scope == "related" and check.evidence):
                continue
            requirement = requirements.get(check.requirement_id)
            kind = requirement.kind if requirement else "fact"
            if check.scope == "related":
                key = ("related", check.related_entity, check.status == "not_met" and check.strength == "hard")
            elif kind == "delivery":
                key = ("delivery", None, False)
            else:
                key = ("offer" if kind in {"price", "stock", "availability", "booking"} else "facts", None, False)
            groups.setdefault(key, []).append(check)
        candidate_queries = []
        for (category, entity, replacement), checks in sorted(groups.items(), key=lambda item: (
            {"related": 0, "offer": 0, "delivery": 1, "facts": 2}[item[0][0]])):
            gap = "; ".join(dict.fromkeys(check.requirement for check in checks))
            name = entity or finding.name
            if replacement:
                name = f"Hotels in {finding.name}" if re.search(r"hotel", gap, re.I) else f"Alternatives in {finding.name}"
                objective = f"Find a replacement for {entity or 'the rejected related option'} satisfying: {gap}."
            elif category == "delivery":
                from urllib.parse import urlsplit
                seller = urlsplit(finding.source_url or "").hostname or finding.name
                name = f"{seller} shipping delivery policy"
                objective = f"Find the seller's delivery/pickup policy applicable to {finding.name}, not another product specification page: {gap}."
            else:
                objective = f"Find direct authoritative evidence for {name}: {gap}."
            text = f"{name} {finding.name if entity else ''} {contract.destination or ''}: {gap}"
            priority = 0 if any(check.strength == "hard" for check in checks) else 1
            candidate_queries.append((priority, (text, f"{objective} Original task: {contract.original_request}", gap)))
        per_candidate.append(candidate_queries)
    # Cover hard gaps across candidates before spending slots on preferences.
    for priority in (0, 1):
        groups = [[query for level, query in group if level == priority] for group in per_candidate]
        for index in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if index < len(group):
                    add_query(*group[index])
                if len(queries) == 4:
                    return queries
    return queries


def scope_known_seller_query(query, finding, contract, sources):
    if (not contract.shopping or finding.source_kind != "product" or query.include_domains or query.exclude_domains
            or not any(source["url"] == finding.source_url for source in sources or [])):
        return query
    text = query.query + " " + (query.gap or "")
    if re.search(r"\b(?:alternatives?|replacement|different retailers|other sellers|new sellers)\b", text, re.I):
        return query
    requirements = {requirement.id: requirement for requirement in contract.requirements}
    unresolved = {requirements[check.requirement_id].kind for check in finding.checks
                  if check.requirement_id in requirements and check.status == "unknown" and check.strength == "hard"}
    stock = bool(unresolved.intersection({"stock", "availability"}) and re.search(
        r"\b(?:stock|availability|available|varast\w*|saatav\w*)\b", text, re.I))
    delivery = bool("delivery" in unresolved and re.search(r"\b(?:shipping|delivery|pickup|toimit\w*|nouto\w*)\b", text, re.I))
    domain = source_domain(finding.source_url or "")
    if not domain or not (stock or delivery):
        return query
    return query.model_copy(update={"include_domains": [domain]})


def prioritize_refinement_queries(planned, answer: dict, contract: ResearchContract, attempted: set[str], sources=None):
    from app.orchestration.interpretation import query_key

    try:
        parsed = GroundedAnswer.model_validate(answer)
    except ValueError:
        return [query for query in planned if query_key(query) not in attempted][:4]
    if sources is not None:
        parsed = apply_contract(parsed, sources, contract)
    hard_contract = contract.model_copy(update={"requirements": [r for r in contract.requirements if r.strength == "hard"],
                                                "requested_count": None})
    reserved = fallback_gap_queries(parsed.model_dump(), hard_contract, attempted, sources)

    def normalized(text):
        return " ".join(re.findall(r"\w+", "".join(char for char in normalize("NFKD", text.casefold()) if not combining(char))))

    targets, target_findings = [], []
    for finding in parsed.findings:
        unresolved = [check for check in finding.checks if check.strength == "hard" and check.status != "met"
                      and not getattr(check, "refinement_excluded", False)]
        if unresolved:
            names = [finding.name, *[check.related_entity for check in unresolved if check.related_entity]]
            targets.append([normalized(name) for name in names])
            target_findings.append(finding)

    def covered(query):
        text = " " + normalized(query.query + " " + (query.gap or "")) + " "
        return {index for index, names in enumerate(targets) if any(" " + name + " " in text for name in names)}

    selected = replacement_gap_queries(parsed.model_dump(), contract, attempted, sources)
    seen = set(attempted) | {query_key(query) for query in selected}

    def prepare(query):
        matches = covered(query)
        if len(matches) == 1:
            return scope_known_seller_query(query, target_findings[next(iter(matches))], contract, sources)
        return query

    def add(query):
        query = prepare(query)
        key = query_key(query)
        if len(selected) < 4 and key not in seen:
            selected.append(query)
            seen.add(key)

    for fallback in reserved:
        if (fallback.gap or "").startswith("Replacement for "):
            add(fallback)
            continue
        needed = covered(fallback)
        matching = next((query for query in planned if query_key(prepare(query)) not in seen and needed.intersection(covered(query))), None)
        if matching:
            matching = matching.model_copy(update={
                "objective": f"Resolve required evidence first: {fallback.gap}. {matching.objective}"[:2000],
                "gap": f"{fallback.gap}; {matching.gap or ''}"[:1000],
            })
        add(matching or fallback)
    for query in planned:
        add(query)
    if len(selected) < 4:
        for query in fallback_gap_queries(parsed.model_dump(), contract, seen, sources):
            if query.gap and any(query.gap in (old.gap or "") and covered(query).intersection(covered(old)) for old in selected):
                continue
            add(query)
    return selected


def prepared_action(contract: ResearchContract, answer: GroundedAnswer, sources: list[dict]) -> dict:
    answer = apply_contract(answer, sources, contract)
    for finding in answer.findings:
        conflicting_related_checks(finding, sources)
    gaps = evidence_gaps(answer, sources, contract)
    if contract.clarification_question:
        return {"kind": "clarification", "text": contract.clarification_question, "gaps": gaps}
    if answer.knowledge and answer.knowledge.points:
        return {"kind": "none", "gaps": [], "external_action": False}
    if contract.artifact in {"checklist", "message_draft", "plan", "form_draft"}:
        facts = []
        for finding in answer.findings:
            for check in finding.checks:
                evidence = matching_evidence(check.evidence, sources)
                if check.status == "met" and evidence:
                    facts.append(f"- {plain(finding.name)}: {plain(check.statement)} {links(evidence)}")
        missing = list(dict.fromkeys([*contract.missing_information, *[g["requirement"] for g in gaps]]))
        label = {"checklist": "Checklist", "message_draft": "Message draft", "plan": "Plan", "form_draft": "Form draft"}[contract.artifact]
        body = [label + " (prepared only)"]
        if contract.artifact == "message_draft":
            body.append("Hello, I’m enquiring about the following options and details:")
        body.extend(facts or ["No candidate details have been verified yet."])
        if missing:
            body.append("Details to confirm:\n" + "\n".join("- " + plain(value) for value in missing))
        return {"kind": contract.artifact, "text": "\n\n".join(body), "gaps": gaps, "external_action": False}
    return {"kind": "research" if gaps else "none", "gaps": gaps, "missing_information": contract.missing_information}


def finish_research_result(result, contract, previous_output=None):
    raw = result.debug.get("output")
    if not raw or not contract:
        return result
    try:
        answer = GroundedAnswer.model_validate_json(raw)
        if previous_output:
            answer, merge = merge_answers(GroundedAnswer.model_validate(previous_output), answer, result.sources, contract)
            result.debug["refinement"] = merge
        else:
            answer = apply_contract(answer, result.sources, contract)
        related_conflicts = sum(conflicting_related_checks(finding, result.sources) for finding in answer.findings)
        if related_conflicts:
            result.debug.setdefault("refinement", {"carried_checks": 0, "conflicts": 0})["conflicts"] += related_conflicts
    except (ValueError, TypeError):
        return result
    reply, issues = render_answer(answer, result.sources, contract)
    coverage = answer_coverage(answer, result.sources, contract)
    knowledge_points = [point for point in answer.knowledge.points if matching_evidence(point.evidence, result.sources)] if answer.knowledge else []
    coverage["knowledge_points"] = len(knowledge_points)
    coverage["supported_checks"] += len(knowledge_points)
    coverage["supported_hard_checks"] += sum(point.basis == "confirmed" for point in knowledge_points)
    missing_preferences = missing_preference_ids(answer, result.sources, contract)
    coverage["unresearched_preferences"] = sorted(missing_preferences)
    leads = result.debug.get("coverage", {}).get("discovery_leads", [])
    coverage["discovery_leads"] = leads
    coverage["gaps"].extend(leads)
    action = prepared_action(contract, answer, result.sources)
    if contract.missing_information:
        reply += "\n\nStill needed: " + "; ".join(plain(value) for value in contract.missing_information) + "."
    result.reply = reply
    result.debug.update({"output": answer.model_dump_json(), "coverage": coverage, "validation_issues": issues,
                         "next_action": action, "has_gaps": bool(issues or missing_preferences or action["kind"] == "clarification"
                         or coverage["verified"] < coverage["retained"]
                         or (coverage["requested"] and coverage["retained"] < coverage["requested"])
                         or (not coverage["retained"] and not answer.response and not knowledge_points))})
    return result


def refinement_context(result) -> str:
    action = result.debug.get("next_action", {})
    coverage = result.debug.get("coverage", {})
    gaps = list(action.get("gaps") or coverage.get("gaps", []))
    gaps.extend(lead for lead in coverage.get("discovery_leads", []) if lead not in gaps)
    requested = coverage.get("requested")
    retained = coverage.get("retained", 0)
    verified = coverage.get("verified", retained)
    if requested and verified < requested:
        gaps.insert(0, {"requirement": "Find additional distinct candidates satisfying the original requirements",
                        "requested": requested, "retained": retained, "verified": verified, "missing": requested - verified})
    return ("Continue the original task without changing its requirements. Search only these unresolved facts. "
            "Use exact candidate and related-entity names, preserve verified facts, and investigate conflicting versions/dates. "
            "Keep candidate names unchanged when improving the same option. Do not repeat searches that added no useful evidence. "
            "Replace candidates with verified hard failures, such as an over-budget offer, instead of filling their remaining specifications. "
            "Preference gaps mean that preference has no evidence for any candidate; investigate it without treating a known preference failure as disqualifying. "
            "Return no queries if none can help. Gaps:\n" + json.dumps(gaps, ensure_ascii=False)[:12000])
