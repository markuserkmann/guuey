"""Opt-in live HTTP/worker/persistence evaluation using isolated evaluation chats."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import tempfile
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from markdown_it import MarkdownIt

CASES = {
    "estonia_hotels": "Get me top 5 holiday spots in estonia on an budget and hotels for them format them into an table",
    "loyly_price": "What does an adult public sauna session at Löyly Helsinki cost, how long is it, and what is included? Use the operator's current information and give the booking link.",
    "finland_laptops": "Find 3 laptops I can buy in Finland for under €700, with at least 16 GB RAM and a 512 GB SSD. Show exact configuration, current price, stock and seller links.",
    "rent_increase_fi": "My landlord in Helsinki says my rent goes up 9% from 1 January 2027. My lease is indefinite and has a clause tying the rent to the cost-of-living index. Is this increase allowed, and what can I do? Draft a short message to the landlord asking how the increase was calculated.",
    "kela_benefits": "We are a family in Espoo with two children aged 2 and 7. One parent works full-time, the other became unemployed last month. Which Kela benefits or other support might we be missing, roughly how much is each, and what do we need to apply?",
    "vegan_anniversary": "Find 3 fully vegan (not just vegan-friendly) restaurants in Helsinki for an anniversary dinner for two next Saturday at 19:00, total under €150. Check whether they're open then and prepare a reservation request for the best one.",
    "flight_disruption": "My Finnair flight from Helsinki to Barcelona on 10 November 2026 was cancelled 4 hours before departure. They rebooked me and I arrived 7 hours later than planned. What compensation and care am I entitled to under EU rules, and how do I claim from Finnair? Prepare the claim text.",
    "business_info": "Give me the business ID (Y-tunnus), registered address, line of business and current CEO of Supercell Oy, with official sources.",
    "recycling_errand": "Where in Espoo can I drop off an old laptop and a broken microwave for free, and which of those places are open this Saturday? Give addresses and hours.",
    "followup_laptops": "Actually make the budget €900 and it must have a Finnish/Nordic keyboard layout.",
}


def quota_error(record):
    """Stop the protocol after a provider rejects its usage budget."""
    errors = [record.get("error"), (record.get("debug") or {}).get("error")]
    for stage in (record.get("debug") or {}).get("stages", []):
        errors.extend([stage.get("error"), stage.get("retrieval_errors")])
    return bool(re.search(r"quota|billing|resource.exhausted|insufficient.(?:credit|balance)|request.limit|\b429\b|\b402\b",
                          json.dumps(errors, default=str), re.I))


def credentials():
    values = dict(os.environ)
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values.setdefault(key.strip(), value.strip().strip("\"'"))
    token = values.get("AGENT_API_TOKEN")
    if not token:
        raise SystemExit("AGENT_API_TOKEN is required.")
    secrets = [values[key] for key in ("AGENT_API_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "EXA_API_KEY") if values.get(key)]
    for key in ("AGENT_API_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_PLANNING_MODEL", "GEMINI_RESEARCH_MODEL", "GEMINI_VERIFICATION_MODEL"):
        if values.get(key):
            os.environ.setdefault(key, values[key])
    return token, secrets


def write_json(path, value, secrets):
    encoded = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    for secret in secrets:
        encoded = encoded.replace(secret, "[redacted]")
    path.write_text(encoded)


def configured_models():
    from app.model_selection import base_model_name, stage_model_name
    return {"configuration_scope": "Evaluator process; server stage models are recorded separately from returned debug.",
            "base": base_model_name(), **{stage: stage_model_name(stage) for stage in ("planning", "research", "verification")},
            "decision": os.getenv("GEMINI_DECISION_MODEL", "gemini-decision-1")}


def observed_models(debug):
    return [{"round": index, "planning": (stage.get("search_plan") or {}).get("model"),
             "research": stage.get("model"), "verification": stage.get("verification_model") or (stage.get("verification") or {}).get("model")}
            for index, stage in enumerate(debug.get("stages", []), 1)]


def final_structured(record):
    debug = record.get("debug") or record.get("run", {}).get("debug", {})
    state = debug.get("task_state") or {}
    if isinstance(state.get("answer"), dict):
        return state["answer"], state.get("contract") or {}, "task_state.answer"
    stages = debug.get("stages", [])
    if len(stages) == 1:
        output = stages[0].get("output")
        try:
            answer = json.loads(output) if isinstance(output, str) else output
            if isinstance(answer, dict):
                return answer, stages[0].get("research_contract") or {}, "single completed stage"
        except (ValueError, TypeError):
            pass
    # Older workers retained the highest scoring round, which need not be the last.
    from app.orchestration.grounding import GroundedAnswer, render_answer
    from app.orchestration.contracts import ResearchContract
    result = record.get("run", {}).get("result") or {}
    for index, stage in enumerate(stages):
        try:
            output = stage.get("output")
            answer = json.loads(output) if isinstance(output, str) else output
            contract = stage.get("research_contract") or {}
            rendered, _ = render_answer(GroundedAnswer.model_validate(answer), result.get("sources", []), ResearchContract.model_validate(contract))
            if rendered == result.get("reply"):
                return answer, contract, f"stage {index + 1} matched persisted reply"
        except (ValueError, TypeError, KeyError):
            continue
    return {}, {}, "unavailable: cannot identify persisted structured answer"


def acceptance_metrics(record):
    """Deterministic task-completion gates, not an independent factual accuracy score."""
    from app.orchestration.passages import find_passage, visible
    answer, contract, origin = final_structured(record)
    result = record.get("run", {}).get("result") or {}
    sources = {source["url"]: source for source in result.get("sources", [])}
    requirements = {r["id"]: r for r in contract.get("requirements", [])}
    reference = record.get("run", {}).get("updated_at") or record.get("started_at")
    try:
        evaluated_at = datetime.fromisoformat(reference)
    except (ValueError, TypeError):
        evaluated_at = None

    def fresh(source):
        if source.get("retrieval") != "live" or evaluated_at is None:
            return False
        timestamps = [source.get("observed_at") or source.get("retrieved_at")]
        if source.get("crawled_at"):
            timestamps.append(source["crawled_at"])
        try:
            return all(0 <= (evaluated_at - datetime.fromisoformat(value)).total_seconds() < 300 for value in timestamps)
        except (ValueError, TypeError):
            return False

    def passages(check):
        matches = []
        for evidence in check.get("evidence", []):
            source = sources.get(evidence.get("source_url"))
            if source and evidence.get("quote"):
                match = find_passage(evidence["quote"], source.get("text", ""), evidence.get("passage_id"), source["url"])
                if match:
                    matches.append((source, visible(source.get("title", "") + "\n" + match.context)))
        return matches

    def named(name, text):
        return bool(name and re.search(r"(?<!\w)" + re.escape(visible(name)) + r"(?!\w)", visible(text), re.I))

    def hotel_named(name, text):
        if not name:
            return False
        core = re.sub(r"^(?:hotel|hotell)\s+", "", name, flags=re.I)
        return named(name, text) or (core != name and len(core) >= 4 and named(core, text))

    def supported(check):
        return check.get("status") == "met" and bool(passages(check))

    findings = answer.get("findings", [])
    candidates = []
    for finding in findings:
        checks = {c.get("requirement_id"): c for c in finding.get("checks", [])}
        hard = [r for r in requirements.values() if r.get("strength", "hard") == "hard"]
        related = [c for c in checks.values() if
                   (c.get("scope") == "related" or requirements.get(c.get("requirement_id"), {}).get("scope") == "related")
                   and re.search(r"hotel|hostel|accommod|lodg|stay|guesthouse", " ".join(str(value) for value in
                       (c.get("requirement_id", ""), requirements.get(c.get("requirement_id"), {}).get("description", ""))), re.I)]
        place = re.sub(r"\s+(?:old town|city cent(?:re|er)|national park|island)$", "", finding.get("name", ""), flags=re.I)
        named_stays, complete_related = [], []
        for check in related:
            entity = check.get("related_entity")
            evidence = passages(check)
            if entity and any(hotel_named(entity, context) for _, context in evidence):
                named_stays.append(entity)
                requires_hotel = record.get("case_id") == "estonia_hotels" or requirements.get(check.get("requirement_id"), {}).get("description", "").startswith("Named hotel")
                wrong_hotel_type = requires_hotel and re.search(
                    r"\b(?:hostel|guesthouse|motel|motell)\b", entity, re.I)
                is_required_stay = requirements.get(check.get("requirement_id"), {}).get("strength", check.get("strength", "hard")) == "hard"
                if is_required_stay and supported(check) and not wrong_hotel_type and any(hotel_named(entity, context) and named(place, context) for _, context in evidence):
                    complete_related.append(check.get("requirement_id"))
        known = {}
        for kind in ("price", "stock", "delivery"):
            relevant = [c for rid, c in checks.items() if requirements.get(rid, {}).get("kind") == kind]
            known[kind] = any(supported(c) for c in relevant)
            known[kind + "_current"] = any(supported(c) and any(fresh(source) for source, _ in passages(c)) for c in relevant)
        hard_supported = [r["id"] for r in hard if supported(checks.get(r["id"], {}))]
        budget_supported = []
        budget_rejections = {}
        for identifier, check in checks.items():
            requirement = requirements.get(identifier, {})
            label = identifier + " " + requirement.get("description", "")
            if not re.search(r"budget|afford|cheap|low.cost|value", label, re.I):
                continue
            if not supported(check):
                budget_rejections[identifier] = "No met check with a matching source passage."
                continue
            quoted = " ".join(e.get("quote", "") for e in check.get("evidence", [])
                              if e.get("source_url") in sources and e.get("quote")
                              and find_passage(e["quote"], sources[e["source_url"]].get("text", ""), e.get("passage_id"), e["source_url"]))
            budget_text = re.sub(r"\bfree\s+(?:wi[ -]?fi|internet)(?:\s+(?:access|in public areas))?", "", quoted, flags=re.I)
            primary_hotel_claim = check.get("scope", requirement.get("scope")) != "related" and re.search(
                r"\bhotel\b|\bsuites\b|\brooms?\b", quoted, re.I)
            if primary_hotel_claim and named_stays and not any(hotel_named(entity, quoted) for entity in named_stays):
                budget_rejections[identifier] = "Primary budget claim describes another hotel rather than the recommended stay."
                continue
            useful_budget_fact = re.search(r"budget.friendly|affordable|inexpensive|cheap|low.cost|low.budget|"
                r"\bfree\b|no (?:entrance|admission|entry) fees?|discount|included breakfast|breakfast included", budget_text, re.I)
            monetary_claim = re.search(r"€|\bEUR(?:\b|\s*\d)|[$£]\s*\d|\d\s*(?:euros?|USD|GBP)\b", check.get("statement", ""), re.I)
            if not useful_budget_fact and not monetary_claim:
                budget_rejections[identifier] = "No explicit affordability fact or concrete cost; mid-range or undefined emoji tiers alone are insufficient."
                continue
            # An old quoted rate does not establish affordability for a current trip.
            if monetary_claim and not any(fresh(source) for source, _ in passages(check)):
                budget_rejections[identifier] = "Monetary claim lacks fresh evidence at the recorded evaluation time."
                continue
            if check.get("scope", requirement.get("scope")) == "related":
                entity = check.get("related_entity")
                if not entity or not any(hotel_named(entity, context) and named(place, context) for _, context in passages(check)):
                    budget_rejections[identifier] = "Related budget evidence does not establish the named stay and destination relationship."
                    continue
            budget_supported.append(identifier)
        candidates.append({"name": finding.get("name"), "source_url": finding.get("source_url"),
            "source_kind": finding.get("source_kind"), "hard_requirement_count": len(hard),
            "supported_hard_requirements": hard_supported, "all_hard_requirements_supported": bool(hard) and len(hard_supported) == len(hard),
            "named_related_stays_with_passages": sorted(set(named_stays)),
            "fully_supported_related_checks": complete_related, "supported_budget_requirements": budget_supported,
            "supported_budget_preferences": [identifier for identifier in budget_supported if requirements.get(identifier, {}).get("strength") == "preference"],
            "budget_evidence_rejections": budget_rejections, "known": known})
    tokens = MarkdownIt().enable("table").parse(result.get("reply", ""))
    in_body, rows = False, 0
    for token in tokens:
        if token.type in {"tbody_open", "tbody_close"}:
            in_body = token.type == "tbody_open"
        rows += int(in_body and token.type == "tr_open")
    common = {"structured_answer_origin": origin, "contract": contract, "evaluated_at": reference,
              "method": "Final validated statuses plus actual supplied quote passages and task-specific gates. No accuracy percentage; semantic audit is still required.",
              "candidate_count": len(candidates), "candidates": candidates, "markdown_table_data_rows": rows}
    case = record.get("case_id")
    if case == "estonia_hotels":
        named_count = sum(bool(c["named_related_stays_with_passages"]) for c in candidates)
        required_related = {r["id"] for r in requirements.values() if r.get("scope") == "related" and r.get("strength", "hard") == "hard"}
        supported_count = sum(bool(required_related) and required_related.issubset(c["fully_supported_related_checks"]) for c in candidates)
        budget_count = sum(bool(c["supported_budget_requirements"]) for c in candidates)
        criteria = {"exactly_five_table_rows": rows == 5, "exactly_five_distinct_destinations": len({c["name"] for c in candidates}) == 5,
                    "five_named_stays_with_passages": named_count == 5, "five_fully_supported_destination_stay_checks": supported_count == 5,
                    "budget_evidence_for_each_destination": budget_count == 5}
        common.update(named_stay_destination_count=named_count, fully_supported_related_destination_count=supported_count,
                      fully_supported_related_check_count=sum(len(c["fully_supported_related_checks"]) for c in candidates),
                      budget_supported_destination_count=budget_count)
    elif case == "loyly_price":
        semantic_checks = {"price": [], "duration": [], "inclusions": [], "booking": []}
        full_services = 0
        for finding, candidate in zip(findings, candidates):
            local = {key: [] for key in semantic_checks}
            for check in finding.get("checks", []):
                requirement = requirements.get(check.get("requirement_id"), {})
                label = " ".join(str(requirement.get(k, "")) for k in ("id", "description", "kind"))
                for key, pattern in {"price": r"price|cost", "duration": r"duration|session length", "inclusions": r"includ|inclusion|amenit", "booking": r"booking|reservation"}.items():
                    if re.search(pattern, label, re.I):
                        semantic_checks[key].append(check)
                        local[key].append(check)
            if candidate["all_hard_requirements_supported"] and all(any(supported(c) for c in checks) for checks in local.values()) and any(supported(c) and any(fresh(s) for s, _ in passages(c)) for c in local["price"]):
                full_services += 1
        criteria = {f"{key}_supported": any(supported(c) for c in checks) for key, checks in semantic_checks.items()}
        criteria["current_price_evidence"] = any(supported(c) and any(fresh(s) for s, _ in passages(c)) for c in semantic_checks["price"])
        criteria["all_hard_requirements_supported"] = any(c["all_hard_requirements_supported"] for c in candidates)
        criteria["one_complete_service"] = full_services >= 1
        common["fully_supported_service_count"] = full_services
    elif case in {"finland_laptops", "followup_laptops"}:
        from app.orchestration.grounding import category_source, comparison_source
        budget = contract.get("budget") or {}
        known_counts = {kind: sum(c["known"][kind] for c in candidates) for kind in ("price", "stock", "delivery", "price_current", "stock_current")}
        complete = [c for c in candidates if c["source_kind"] == "product" and c["source_url"] in sources and c["all_hard_requirements_supported"]
                    and not category_source(c["source_url"]) and not comparison_source(c["source_url"])
                    and all(c["known"][k] for k in ("price_current", "stock_current", "delivery"))]
        distinct_offers = len({c["source_url"] for c in complete})
        labels = " ".join(r.get("description", "") for r in requirements.values())
        expected_budget = 900 if case == "followup_laptops" else 700
        criteria = {"three_fully_verified_current_offers": distinct_offers == 3, f"exact_under_{expected_budget}_eur_contract": budget.get("amount") == expected_budget and budget.get("currency") == "EUR" and budget.get("operator") == "lt",
                    "ram_and_storage_constraints_preserved": bool(re.search(r"16\s*(?:gb|gt)", labels, re.I) and re.search(r"512\s*(?:gb|gt)", labels, re.I))}
        if case == "followup_laptops":
            keyboard_ids = {r["id"] for r in requirements.values() if re.search(r"keyboard|näppäimist", r.get("description", ""), re.I)}
            criteria["keyboard_layout_supported_for_every_offer"] = bool(keyboard_ids) and len(findings) == 3 and all(
                any(supported(c) for c in finding.get("checks", []) if c.get("requirement_id") in keyboard_ids) for finding in findings)
            criteria["same_chat_predecessor_recorded"] = bool(record.get("predecessor_run_id"))
        common.update(known_requirement_counts=known_counts, fully_verified_current_offer_count=distinct_offers)
    elif case in {"rent_increase_fi", "kela_benefits", "flight_disruption", "business_info"}:
        from urllib.parse import urlsplit
        knowledge = answer.get("knowledge") or {}
        points = knowledge.get("points", [])
        official_hosts = {"rent_increase_fi": {"finlex.fi", "kkv.fi", "kuluttajariita.fi"},
                          "kela_benefits": {"kela.fi", "tyomarkkinatori.fi", "tyj.fi"},
                          "flight_disruption": {"eur-lex.europa.eu", "europa.eu", "traficom.fi"},
                          "business_info": {"ytj.fi", "prh.fi"}}[case]
        grounded = [point for point in points if passages(point)]
        official_points = [point for point in grounded if any(
            (urlsplit(source["url"]).hostname or "").removeprefix("www.") in official_hosts for source, _ in passages(point))]
        criteria = {"structured_cited_knowledge_points": bool(grounded), "official_rule_or_registry_evidence": bool(official_points),
                    "no_shortlist_fallback": "matching shortlist" not in result.get("reply", "").casefold()}
        if case in {"rent_increase_fi", "flight_disruption"}:
            criteria["prepared_draft"] = bool(knowledge.get("draft"))
            criteria["next_steps"] = bool(knowledge.get("steps"))
        if case in {"rent_increase_fi", "kela_benefits", "flight_disruption"}:
            criteria["conditional_caveats"] = bool(knowledge.get("caveats"))
        if case == "business_info":
            text = " ".join(point.get("statement", "") for point in grounded)
            criteria["requested_business_fields_present"] = all(re.search(pattern, text, re.I) for pattern in (
                r"\b\d{7}-\d\b", r"address|osoite", r"business|toimiala", r"CEO|chief executive|toimitusjohtaja"))
        common["semantic_audit_required"] = "These are structural evidence gates; independently audit eligibility, law, amounts, registry fields and dates."
    elif case in {"vegan_anniversary", "recycling_errand"}:
        required_patterns = ({"fully_vegan": r"fully vegan|100.?%.?vegan|täysin vegaan", "hours": r"hours|open|opening|aukiolo",
                              "price": r"price|budget|cost", "booking": r"booking|reservation"} if case == "vegan_anniversary" else
                             {"location": r"Espoo|address|osoite", "hours": r"hours|open|opening|aukiolo", "free": r"free|maksuton|no charge"})
        complete = []
        for finding in findings:
            complete.append(all(any(supported(c) and re.search(pattern, c.get("requirement", "") + " " + requirements.get(c.get("requirement_id"), {}).get("description", ""), re.I)
                                   for c in finding.get("checks", [])) for pattern in required_patterns.values()))
        criteria = {"requested_places_with_supported_facts": sum(complete) >= (3 if case == "vegan_anniversary" else 1),
                    "relative_date_resolved": any(re.search(r"\d{4}-\d{2}-\d{2}", date) for date in contract.get("dates", []))}
        if case == "vegan_anniversary":
            criteria["reservation_request_prepared"] = bool((answer.get("knowledge") or {}).get("draft") or re.search(r"reservation request|varauspyyntö", result.get("reply", ""), re.I))
        common["semantic_audit_required"] = "Independently audit exact date, hours, holiday exceptions, prices and venue type."
    else:
        criteria = {"known_case": False}
    return {**common, "criteria": criteria, "task_acceptance_passed": bool(criteria) and all(criteria.values())}


def diagnostics(debug):
    stages = debug.get("stages", [])
    usage = [debug.get("routing_usage", {})]
    rounds = []
    for index, stage in enumerate(stages, 1):
        plan = stage.get("search_plan") or {}
        usage.extend([stage.get("usage", {}), plan.get("usage", {})])
        rounds.append({"round": index, "queries": (plan.get("output") or {}).get("queries", []),
                       "contract": stage.get("research_contract"), "retrieval": stage.get("retrieval"),
                       "coverage": stage.get("coverage"), "verification_rejections": (stage.get("verification") or {}).get("rejected", []),
                       "validation_issues": stage.get("validation_issues", []), "has_gaps": stage.get("has_gaps")})
        events = [e for e in debug.get("model_events", []) if e.get("round") == index]
        completed = [e for e in events if e.get("status") == "completed"]
        timing = {name + "_s": round(sum(e.get("elapsed_seconds", 0) for e in completed if e.get("stage") == name), 3)
                  for name in ("planning", "answering", "verifying")}
        retrieval = stage.get("retrieval") or {}
        intervals = []
        for event in completed:
            if event.get('stage') == 'retrieval_request' and event.get('run_elapsed_s') is not None:
                end = event['run_elapsed_s']
                intervals.append((end - event.get('elapsed_seconds', 0), end))
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        timing["retrieval_s"] = round(sum(end - start for start, end in merged), 3) if merged else retrieval.get("elapsed_seconds")
        generation = stage.get("generation_usage") or stage.get("usage") or {}
        crawled = [url for e in events if e.get("stage") == "retrieval_request" and e.get("status") == "started"
                   and e.get("livecrawl") for url in e.get("urls", [])]
        earlier = {url for e in debug.get("model_events", []) if e.get("stage") == "retrieval_request"
                   and e.get("status") == "started" and e.get("livecrawl") and 0 < e.get("round", 0) < index for url in e.get("urls", [])}
        seen = set(earlier)
        duplicates = 0
        for url in crawled:
            duplicates += url in seen
            seen.add(url)
        coverage = stage.get("coverage") or {}
        try:
            output = json.loads(stage.get('output') or '{}')
        except (ValueError, TypeError):
            output = {}
        hard_supported = sum(c.get('status') == 'met' and c.get('strength', 'hard') == 'hard' and bool(c.get('evidence'))
                             for f in output.get('findings', []) for c in f.get('checks', []))
        rounds[-1].update(timing, generation_input_tokens=generation.get("input_tokens", 0),
                          generation_output_tokens=generation.get("output_tokens", 0),
                          generation_requests=generation.get("requests", 0),
                          contents_calls=sum(e.get("endpoint") == "contents" and e.get("status") == "started" for e in events),
                          livecrawled_urls=len(crawled), duplicate_crawled_urls=duplicates,
                          supported_checks=coverage.get("supported_checks", 0), verified=coverage.get("verified", 0),
                          supported_hard_checks=coverage.get('supported_hard_checks', hard_supported),
                          retained=coverage.get("retained", 0), round_s=stage.get("round_s"))
    usage_reports = [item for item in usage if isinstance(item, dict) and item]
    reported_costs = []
    for item in usage_reports:
        value = item.get("cost")
        if value is None or isinstance(value, bool):
            continue
        try:
            cost = float(value)
        except (ValueError, TypeError):
            continue
        if math.isfinite(cost) and cost >= 0:
            reported_costs.append(cost)
    events = debug.get("model_events", [])
    retrieval_calls = [e for e in events if e.get("stage") == "retrieval_request"]
    exa_costs = [e["cost_dollars"]["total"] for e in retrieval_calls
                 if e.get("status") == "completed" and isinstance(e.get("cost_dollars"), dict)
                 and isinstance(e["cost_dollars"].get("total"), (int, float))]
    return {"router": debug.get("router"), "decision": debug.get("decision"), "rounds": rounds,
            "observed_stage_models": observed_models(debug),
            "completed_model_calls": sum(e.get("status") == "completed" and e.get("stage") not in {"retrieval", "retrieval_request"} for e in events),
            "exa_requests_started": sum(e.get("status") == "started" for e in retrieval_calls),
            "exa_reported_cost_usd": round(sum(exa_costs), 6) if exa_costs else None,
            "provider_usage_reported_cost_usd": round(sum(reported_costs), 6) if reported_costs else None,
            "provider_cost_report_count": len(reported_costs),
            "provider_usage_report_count": len(usage_reports),
            "provider_cost_reporting_complete": bool(usage_reports) and len(reported_costs) == len(usage_reports),
            "cost_scope": "Gemini total includes only explicit finite nonnegative costs in received routing, planning and answer usage reports (answer usage includes verifier); partial reporting is a subtotal, not total spend. None means no cost was reported; zero requires an explicit zero report. Report counts cover received usage objects, not an inferred count of all billable requests. Exa cost sums available completed-request totals; missing costs are not estimated.",
            "model_events": debug.get("model_events", []),
            "next_actions": (stages[-1].get("coverage", {}).get("gaps", []) if stages else []),
            "stop_reason": debug.get("stop_reason"), "error": debug.get("error")}


def performance_summary(record):
    debug = record.get("debug") or {}
    run = record.get("run") or {}
    first = debug.get("first_answer_s")
    if first is None:
        first = next((e["elapsed_seconds"] for e in record.get("transitions", []) if e.get("phase") == "answer_ready"), None)
    return {"first_answer_s": first, "wall_s": debug.get("wall_s", record.get("worker_seconds_observed", record.get("elapsed_seconds"))),
            "stop_reason": debug.get("stop_reason"), "final_phase": run.get("phase"),
            "budget_exhausted": int(run.get("phase") == "budget_exhausted"),
            "rounds": diagnostics(debug)["rounds"]}


def run_replay(case_id, args, token, secrets):
    from fastapi.testclient import TestClient
    from app import main as api, worker, research
    from app.runs import RunStore
    from app.orchestration import orchestrator

    captured_path = args.replay_from / f"{case_id}.json"
    captured = json.loads(captured_path.read_text())
    metadata = {s["url"]: s for event in captured["debug"].get("model_events", []) for s in event.get("sources", [])}
    pool = {s["url"]: dict(s) for s in (captured["run"].get("result") or {}).get("sources", [])}
    source_pattern = r"(?:^|\n\n)ID: S\d+\nSource: ([^\n]*)\nURL: ([^\n]+)\nRetrieval: ([^\n]+)\nEvidence: ([\s\S]*?)(?=\n\nID: S\d+\nSource: |\Z)"
    for stage in captured["debug"].get("stages", []):
        for title, url, retrieval, text in re.findall(source_pattern, stage.get("input", "")):
            pool[url] = {**metadata.get(url, {}), "title": title, "url": url, "retrieval": retrieval, "text": text}
    for source in pool.values():
        source.setdefault("source_id", research.stable_source_id(source["url"]))
    calls = []

    def replay_search(query, **options):
        captured_sources = list(pool.values())
        calls.append({"operation": "captured_search", "query": query, "options": options, "returned": len(captured_sources)})
        return [dict(source) for source in captured_sources]

    def replay_extract(urls, *, limit=3, **options):
        selected = [dict(pool[url]) for url in urls[:limit] if url in pool]
        calls.append({"operation": "captured_extract", "urls": urls[:limit], "options": options, "returned": len(selected)})
        if not selected:
            raise research.SearchUnavailable("Replay has no captured content for the requested URLs.")
        return selected

    started = time.monotonic()
    record = {"evaluation": True, "case_id": case_id, "message": CASES[case_id], "mode": args.mode,
              "started_at": datetime.now(UTC).isoformat(), "evaluation_mode": "captured Exa evidence with live Gemini; isolated TestClient and real worker.execute",
              "capture_path": str(captured_path), "capture_sha256": hashlib.sha256(captured_path.read_bytes()).hexdigest(),
              "captured_source_count": len(pool), "freshness_policy": "Original retrieval and timestamps preserved. No clock patch or freshness refresh.",
              "retrieval_limitations": "Returns the full captured document pool in original order; actual orchestrator selection limits still apply. Query/domain/date filters cannot discover new pages. Exa network requests are blocked by an assertion.",
              "transitions": []}
    with tempfile.TemporaryDirectory(prefix="guuey-pipeline-replay-") as temporary:
        with patch.dict(os.environ, {"CHAT_DATABASE_PATH": str(Path(temporary) / "evaluation.sqlite3"),
                                     "SOURCE_CACHE_PATH": str(Path(temporary) / "research-cache.sqlite3")}), \
             patch.object(orchestrator, "search", replay_search), patch.object(orchestrator, "extract_pages", replay_extract), \
             patch.object(research, "_request", side_effect=AssertionError("External Exa requests are forbidden during replay")):
            api.get_store.cache_clear()
            api.get_runs.cache_clear()
            try:
                with TestClient(api.app, headers={"Authorization": f"Bearer {token}"}) as client:
                    created = client.post("/api/chats")
                    created.raise_for_status()
                    chat_id = created.json()["id"]
                    response = client.post(f"/api/chats/{chat_id}/runs", json={"message": CASES[case_id], "mode": args.mode,
                        "timezone": args.timezone, "request_id": str(uuid4())})
                    response.raise_for_status()
                    run_id = response.json()["id"]
                    record.update(chat_id=chat_id, run_id=run_id)
                    claimed = RunStore().claim()
                    if not claimed or claimed["id"] != run_id:
                        raise RuntimeError("Isolated worker did not claim the submitted run")
                    worker.execute(run_id)
                    run = client.get(f"/api/runs/{run_id}").json()
                    refreshed = client.get(f"/api/chats/{chat_id}").json()
                    latest = client.get(f"/api/chats/{chat_id}/runs/latest").json()
                    debug = client.get(f"/api/runs/{run_id}/debug").json()
                    answer = (run.get("result") or {}).get("reply", "")
                    saved = next((m for m in reversed(refreshed["messages"]) if m["role"] == "assistant"), None)
                    sources = (run.get("result") or {}).get("sources", [])
                    links = set(re.findall(r"\]\(<?(https?://[^)>]+)>?\)", answer))
                    parsed = MarkdownIt().enable("table").parse(answer)
                    in_body, rows = False, 0
                    for part in parsed:
                        if part.type in {"tbody_open", "tbody_close"}:
                            in_body = part.type == "tbody_open"
                        rows += int(in_body and part.type == "tr_open")
                    record.update(status=run["status"], run=run, debug=debug, diagnostics=diagnostics(debug), refreshed_chat=refreshed,
                        persistence_checks={"answer_saved": bool(saved and saved["content"] == answer), "latest_run_matches": latest["id"] == run_id,
                            "citation_urls": sorted(links), "unsupplied_citations": sorted(links-{s["url"] for s in sources}),
                            "saved_source_urls_match": bool(saved and {s["url"] for s in saved["sources"]} == {s["url"] for s in sources}),
                            "markdown_table_present": any(t.type == "table_open" for t in parsed), "markdown_table_data_rows": rows})
            except Exception as exc:
                record.update(status="evaluation_error", error={"type": type(exc).__name__, "detail": str(exc)})
            finally:
                api.get_store.cache_clear()
                api.get_runs.cache_clear()
    record.update(elapsed_seconds=round(time.monotonic()-started, 2), replay_retrieval_calls=calls, external_exa_requests=0)
    record["acceptance"] = acceptance_metrics(record)
    record["performance"] = performance_summary(record)
    record["observed_stage_models"] = observed_models(record.get("debug", {}))
    write_json(args.output / f"{case_id}.json", record, secrets)
    print(json.dumps({"case": case_id, "status": record["status"], "elapsed_seconds": record["elapsed_seconds"], "persistence": record.get("persistence_checks")}), flush=True)
    return record


def run_case(case_id, args, token, secrets, *, chat_id=None, predecessor=None):
    started = time.monotonic()
    record = {"evaluation": True, "case_id": case_id, "message": CASES[case_id], "mode": args.mode,
              "started_at": datetime.now(UTC).isoformat(), "timezone": args.timezone, "transitions": [],
              "predecessor_run_id": predecessor}
    path = args.output / f"{case_id}.json"
    with httpx.Client(base_url=args.base_url, headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        def request(method, endpoint, **kwargs):
            for attempt in range(3):
                try:
                    response = client.request(method, endpoint, **kwargs)
                    break
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                    if method != "GET" or attempt == 2:
                        raise
            response.raise_for_status()
            return response.json()
        try:
            chat = {"id": chat_id} if chat_id else request("POST", "/api/chats")
            record["chat_id"] = chat["id"]
            run = request("POST", f"/api/chats/{chat['id']}/runs", json={"message": CASES[case_id], "mode": args.mode,
                "timezone": args.timezone, "request_id": str(uuid4())})
            record["run_id"] = run["id"]
            print(json.dumps({"case": case_id, "run_id": run["id"], "status": "submitted"}), flush=True)
            write_json(path, record, secrets)
            previous = None
            while time.monotonic() - started < args.timeout:
                run = request("GET", f"/api/runs/{run['id']}")
                state = (run["status"], run["phase"])
                if state != previous:
                    record["transitions"].append({"elapsed_seconds": round(time.monotonic() - started, 2), "status": state[0], "phase": state[1]})
                    record["last_run"] = run
                    write_json(path, record, secrets)
                    print(json.dumps({"case": case_id, **record["transitions"][-1]}), flush=True)
                    previous = state
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(1)
            record["run"] = run
            record.pop("last_run", None)
            record["debug"] = request("GET", f"/api/runs/{run['id']}/debug")
            record["diagnostics"] = diagnostics(record["debug"])
            refreshed = request("GET", f"/api/chats/{chat['id']}")
            latest = request("GET", f"/api/chats/{chat['id']}/runs/latest")
            record["refreshed_chat"] = refreshed
            record["latest_run_id"] = latest.get("id") if latest else None
            answer = (run.get("result") or {}).get("reply", "")
            saved = next((m for m in reversed(refreshed["messages"]) if m["role"] == "assistant"), None)
            links = set(re.findall(r"\]\(<?(https?://[^)>]+)>?\)", answer))
            sources = (run.get("result") or {}).get("sources", [])
            table_tokens = MarkdownIt().enable("table").parse(answer)
            in_body, data_rows = False, 0
            for parsed in table_tokens:
                if parsed.type in {"tbody_open", "tbody_close"}:
                    in_body = parsed.type == "tbody_open"
                if in_body and parsed.type == "tr_open":
                    data_rows += 1
            record["persistence_checks"] = {"answer_saved": bool(saved and saved["content"] == answer),
                "latest_run_matches": bool(latest and latest["id"] == run["id"]),
                "citation_urls": sorted(links), "unsupplied_citations": sorted(links - {s["url"] for s in sources}),
                "saved_source_urls_match": bool(saved and {s["url"] for s in saved["sources"]} == {s["url"] for s in sources}),
                "markdown_table_present": any(parsed.type == "table_open" for parsed in table_tokens),
                "markdown_table_data_rows": data_rows}
            record["status"] = run["status"]
            if run["status"] in {"queued", "running"}:
                record["status"] = "evaluation_timeout"
        except Exception as exc:
            record["status"] = "evaluation_error"
            record["error"] = {"type": type(exc).__name__, "detail": str(exc)}
    record["elapsed_seconds"] = round(time.monotonic() - started, 2)
    first_running = next((event["elapsed_seconds"] for event in record["transitions"] if event["status"] == "running"), None)
    record["queue_seconds_observed"] = first_running
    record["worker_seconds_observed"] = round(record["elapsed_seconds"] - first_running, 2) if first_running is not None else None
    record["acceptance"] = acceptance_metrics(record)
    record["performance"] = performance_summary(record)
    record["observed_stage_models"] = observed_models(record.get("debug", {}))
    write_json(path, record, secrets)
    print(json.dumps({"case": case_id, "status": record["status"], "elapsed_seconds": record["elapsed_seconds"], "persistence": record.get("persistence_checks")}), flush=True)
    return record


def run_protocol(args, token, secrets):
    """Bound concurrency and budget; keep each follow-up in its predecessor's chat."""
    cases = [case for case in args.cases if case != "followup_laptops"]
    followup = args.followup or "followup_laptops" in args.cases
    if followup and "finland_laptops" not in cases:
        cases.append("finland_laptops")
    cache_cases = [case for case in ("loyly_price", "business_info", "finland_laptops") if case in cases] if args.cache_checks else []
    planned = args.repetitions * (len(cases) + int(followup)) + len(cache_cases)
    if planned > 26:
        raise ValueError(f"Protocol would submit {planned} runs; the limit is 26.")
    records = []
    for repetition in range(1, args.repetitions + 1):
        trial = copy(args)
        trial.output = args.output / f"trial-{repetition}" if args.repetitions > 1 else args.output
        trial.output.mkdir(parents=True, exist_ok=True)
        for offset in range(0, len(cases), args.concurrency):
            batch = cases[offset:offset + args.concurrency]
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                completed = list(pool.map(lambda case: run_case(case, trial, token, secrets), batch))
            records.extend(completed)
            if any(quota_error(record) for record in completed):
                return records
            for record in completed:
                if record["case_id"] == "finland_laptops" and followup and record.get("status") not in {"evaluation_error", "evaluation_timeout"}:
                    child = run_case("followup_laptops", trial, token, secrets,
                                     chat_id=record["chat_id"], predecessor=record["run_id"])
                    records.append(child)
                    if quota_error(child):
                        return records
                if repetition == 1 and record["case_id"] in cache_cases:
                    warm = copy(trial)
                    warm.output = args.output / "cache-checks"
                    warm.output.mkdir(parents=True, exist_ok=True)
                    cached = run_case(record["case_id"], warm, token, secrets)
                    cached["cache_comparison_run_id"] = record.get("run_id")
                    write_json(warm.output / f"{record['case_id']}.json", cached, secrets)
                    records.append(cached)
                    if quota_error(cached):
                        return records
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=[case for case in CASES if case != "followup_laptops"])
    parser.add_argument("--mode", choices=["auto", "fast", "deep"], default="auto")
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--timezone", default="Europe/Helsinki")
    parser.add_argument("--concurrency", type=int, choices=[1, 2], default=2)
    parser.add_argument("--repetitions", type=int, choices=[1, 2], default=1)
    parser.add_argument("--followup", action="store_true", help="Run the keyboard/budget follow-up in each laptop case's chat")
    parser.add_argument("--cache-checks", action="store_true", help="Immediately repeat sauna, registry and laptop cases once")
    parser.add_argument("--replay-from", type=Path, help="Replay captured Exa sources through isolated app and real worker with live Gemini; never call Exa")
    args = parser.parse_args()
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error("--timezone must be an IANA timezone")
    if args.replay_from and (args.followup or "followup_laptops" in args.cases or args.repetitions != 1 or args.cache_checks):
        parser.error("Replay supports one pass of independent captured cases; follow-ups require live chat history.")
    token, secrets = credentials()
    args.output.mkdir(parents=True, exist_ok=True)
    code = Path(__file__).resolve().parent / "app"
    write_json(args.output / "config.json", {"started_at": datetime.now(UTC).isoformat(), "base_url": args.base_url,
        "app_limits": __import__("app.worker", fromlist=["RESEARCH_LIMITS"]).RESEARCH_LIMITS,
        "mode": args.mode, "cases": args.cases, "timezone": args.timezone, "concurrency": args.concurrency,
        "repetitions": args.repetitions, "followup": args.followup, "cache_checks": args.cache_checks,
        "live_run_limit": 26, "configured_stage_models": configured_models(),
        "source_hashes": {str(p.relative_to(code)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code.rglob("*.py")}}, secrets)
    if args.replay_from:
        records = []
        for case in args.cases:
            record = run_replay(case, args, token, secrets)
            records.append(record)
            if quota_error(record):
                break
    else:
        records = run_protocol(args, token, secrets)
    config_path = args.output / "config.json"
    config = json.loads(config_path.read_text())
    config["observed_stage_models_by_case"] = {r["case_id"]: r.get("observed_stage_models", []) for r in records}
    write_json(config_path, config, secrets)
    write_json(args.output / "summary.json", [{key: r.get(key) for key in ("case_id", "status", "elapsed_seconds", "performance", "chat_id", "run_id", "persistence_checks", "acceptance", "observed_stage_models", "error")} for r in records], secrets)


if __name__ == "__main__":
    main()
