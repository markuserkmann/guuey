from types import SimpleNamespace

from pydantic_ai.usage import RunUsage

from app.orchestration import planner
from app.orchestration.contracts import Requirement, ResearchContract, SearchPlan


def empty_model_plan(monkeypatch, contract):
    monkeypatch.setattr(planner, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(planner, "google_provider", lambda *args: None)
    monkeypatch.setattr(planner, "GoogleModel", lambda *args, **kwargs: object())
    monkeypatch.setattr(planner, "Agent", lambda *args, **kwargs: object())
    monkeypatch.setattr(planner, "call_model", lambda *args, **kwargs: SimpleNamespace(
        output=SearchPlan(contract=contract, queries=[]), usage=RunUsage()))


def test_named_published_price_question_gets_initial_searches_despite_spurious_date_question(monkeypatch):
    contract = ResearchContract(subject="public sauna session", exact_entities=["Löyly Helsinki"], destination="Helsinki",
        clarification_question="Which dates should I check?", requirements=[
            Requirement(id="cost", kind="price", description="Cost of an adult public sauna session"),
            Requirement(id="duration", description="Session duration and inclusions"),
            Requirement(id="booking_link", description="Official booking link")])
    empty_model_plan(monkeypatch, contract)
    result, debug = planner.plan_searches("Original request:\nWhat does an adult sauna session at Löyly Helsinki cost, "
        "how long is it, what is included, and give the booking link?\nUser timezone: UTC. Reference clock: 2026-11-14.")
    assert result.contract.clarification_question is None
    assert len(result.queries) == 4 and debug["initial_fallback"]
    assert any("primary source" in query.objective for query in result.queries)
    assert all("Löyly Helsinki" in query.query for query in result.queries)
    assert any("advertised prices" in query.query for query in result.queries)
    assert any("booking" in query.query for query in result.queries)
    assert not any("Reference clock" in query.query for query in result.queries)


def test_initial_fallback_does_not_override_genuine_material_clarification(monkeypatch):
    empty_model_plan(monkeypatch, ResearchContract(clarification_question="Which city should I search?"))
    result, debug = planner.plan_searches("Find nearby restaurants")
    assert result.queries == [] and not debug["initial_fallback"]


def test_fixed_contract_refinement_empty_plan_stays_exhausted(monkeypatch):
    contract = ResearchContract(subject="public sauna", exact_entities=["Löyly Helsinki"])
    empty_model_plan(monkeypatch, contract)
    result, debug = planner.plan_searches("No useful new evidence query remains", contract)
    assert result.queries == [] and not debug["initial_fallback"]


def test_top_five_related_hotels_fallback_separates_discovery_from_related_evidence(monkeypatch):
    contract = ResearchContract(subject="holiday destinations", destination="Estonia", requirements=[
        Requirement(id="hotels", description="Named hotel for each destination", scope="related"),
        Requirement(id="attractions", description="Things to see in each destination")])
    empty_model_plan(monkeypatch, contract)
    result, _ = planner.plan_searches("Find top 5 holiday destinations in Estonia and hotels for them")
    assert result.contract.requested_count == 5
    assert len(result.queries) == 4
    assert "Discover at least 5" in result.queries[0].objective
    assert any("Named hotel" in query.query for query in result.queries)
    assert all(len(query.query) <= 500 and len(query.objective) <= 2000 for query in result.queries)
