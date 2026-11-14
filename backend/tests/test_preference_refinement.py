from types import SimpleNamespace

import pytest

from app.orchestration.contracts import Requirement, ResearchContract
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck
from app.orchestration.interpretation import query_key
from app.orchestration.refinement import fallback_gap_queries, finish_research_result, missing_preference_ids, prepared_action


def fixture(count=5, known_status=None, matching=True):
    contract = ResearchContract(requested_count=count, requirements=[
        Requirement(id="museum", description="Has museums"),
        Requirement(id="budget", description="Prefer affordable daily transport", strength="preference")])
    sources, findings = [], []
    for index in range(count):
        name, url = f"City {index}", f"https://city{index}.example/"
        text = f"{name} has museums. {name} transport is expensive."
        sources.append({"url": url, "title": name, "text": text, "retrieval": "page"})
        known = bool(index == 0 and known_status)
        findings.append(Finding(name=name, source_url=url, checks=[
            RequirementCheck(requirement_id="museum", requirement="Has museums", status="met",
                statement=f"{name} has museums.", evidence=[Evidence(source_url=url, quote=f"{name} has museums.")]),
            RequirementCheck(requirement_id="budget", requirement="Prefer affordable daily transport", strength="preference",
                status=known_status if known else "unknown", statement=f"{name} transport is expensive." if known else "",
                evidence=[Evidence(source_url=url, quote=f"{name} transport is expensive." if matching else "Fabricated affordability.")] if known else []),
        ]))
    return contract, GroundedAnswer(findings=findings), sources


def test_entire_unresearched_preference_keeps_research_partial_without_disqualifying_candidates():
    contract, answer, sources = fixture()
    result = finish_research_result(SimpleNamespace(reply="", sources=sources, debug={"output": answer.model_dump_json()}), contract)
    assert result.debug["coverage"]["verified"] == 5
    assert result.debug["coverage"]["unresearched_preferences"] == ["budget"]
    assert result.debug["has_gaps"]
    action = result.debug["next_action"]
    assert action["kind"] == "research"
    assert len(action["gaps"]) == 5
    assert all(gap["requirement_id"] == "budget" and gap["strength"] == "preference" for gap in action["gaps"])


@pytest.mark.parametrize("status", ["met", "not_met"])
def test_known_preference_result_does_not_hide_other_candidates_missing_evidence(status):
    contract, answer, sources = fixture(known_status=status)
    assert missing_preference_ids(answer, sources, contract) == {'budget'}
    action = prepared_action(contract, answer, sources)
    assert action["kind"] == "research" and len(action["gaps"]) == 4
    assert all(gap['candidate'] != 'City 0' for gap in action['gaps'])
    assert len(fallback_gap_queries(answer.model_dump(), contract, set(), sources)) == 4
    result = finish_research_result(SimpleNamespace(reply="", sources=sources, debug={"output": answer.model_dump_json()}), contract)
    assert result.debug["has_gaps"]
    assert result.debug["coverage"]["verified"] == 5


def test_nonmatching_preference_quote_does_not_count_as_researched():
    contract, answer, sources = fixture(known_status="not_met", matching=False)
    assert missing_preference_ids(answer, sources, contract) == {"budget"}
    assert fallback_gap_queries(answer.model_dump(), contract, set(), sources)


def test_preference_fallback_queries_are_bounded_and_do_not_repeat():
    contract, answer, sources = fixture()
    attempted = set()
    first = fallback_gap_queries(answer.model_dump(), contract, attempted, sources)
    assert len(first) == 4
    assert all("affordable daily transport" in query.query for query in first)
    attempted.update(query_key(query) for query in first)
    last = fallback_gap_queries(answer.model_dump(), contract, attempted, sources)
    assert len(last) == 1
    attempted.update(query_key(query) for query in last)
    assert not fallback_gap_queries(answer.model_dump(), contract, attempted, sources)


def test_one_supported_preference_does_not_hide_another_unresearched_preference():
    contract, answer, sources = fixture(known_status="not_met")
    contract.requirements.append(Requirement(id="quiet", description="Prefer quiet neighbourhoods", strength="preference"))
    result = finish_research_result(SimpleNamespace(reply="", sources=sources, debug={"output": answer.model_dump_json()}), contract)
    assert result.debug["coverage"]["unresearched_preferences"] == ["budget", "quiet"]
    gaps = result.debug["next_action"]["gaps"]
    assert len([gap for gap in gaps if gap['requirement_id'] == 'budget']) == 4
    assert len([gap for gap in gaps if gap['requirement_id'] == 'quiet']) == 5


def test_reported_preference_is_found_and_does_not_trigger_repeat_research():
    contract, answer, sources = fixture(count=1, known_status="not_met")
    preference = answer.findings[0].checks[1]
    preference.status, preference.basis = "unknown", "reported"
    assert missing_preference_ids(answer, sources, contract) == set()
    action = prepared_action(contract, answer, sources)
    assert action["gaps"] == []
    assert fallback_gap_queries(answer.model_dump(), contract, set(), sources) == []
