from datetime import UTC, datetime

from app.orchestration.contracts import Requirement, ResearchContract, SearchQuery
from app.orchestration.direct_actions import gap_actions
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck
from app.orchestration.interpretation import query_key
from app.orchestration.refinement import replacement_gap_queries, fallback_gap_queries, prioritize_refinement_queries


def rejected_hotel():
    task = ResearchContract(activity="research", original_request="Find a hotel in Tartu", requirements=[
        Requirement(id="hotel", description="Named hotel located in this destination", scope="related")])
    url = "https://hektor.com/"
    source = {"url": url, "title": "Hektor Design Hostel", "text": "# Hektor Design Hostel\n\nHektor Design Hostel is a hostel located in Tartu.",
              "retrieval": "live", "observed_at": datetime.now(UTC).isoformat()}
    answer = GroundedAnswer(findings=[Finding(name="Tartu", source_url=url, checks=[RequirementCheck(
        requirement_id="hotel", requirement="Named hotel located in this destination", status="not_met",
        statement="Hektor Design Hostel is a hostel located in Tartu.", related_entity="Hektor Design Hostel", scope="related",
        evidence=[Evidence(source_url=url, quote="Hektor Design Hostel is a hostel located in Tartu.")])])])
    return task, answer, [source]


def test_rejected_related_hotel_gets_destination_specific_replacement_first():
    task, answer, sources = rejected_hotel()
    planned = [SearchQuery(query="Tallinn budget cafe opening hours", objective="Find further details")]
    selected = prioritize_refinement_queries(planned, answer.model_dump(), task, set(), sources)
    assert selected[0].query == "hotel in Tartu official site"
    assert selected[0].gap.startswith("Replacement for Tartu:")
    assert "Hektor Design Hostel" in selected[0].objective


def test_replacement_search_has_finite_untried_strategies():
    task, answer, sources = rejected_hotel()
    attempted = set()
    first = replacement_gap_queries(answer.model_dump(), task, attempted, sources)
    assert first
    attempted.add(query_key(first[0]))
    second = replacement_gap_queries(answer.model_dump(), task, attempted, sources)
    assert second and query_key(second[0]) not in attempted
    attempted.add(query_key(second[0]))
    assert not replacement_gap_queries(answer.model_dump(), task, attempted, sources)


def test_stock_excluded_from_refinement_does_not_trigger_direct_fetch():
    task = ResearchContract(activity="product")
    answer = {"findings": [{"name": "Laptop A", "source_url": "https://shop.fi/product/a", "checks": [
        {"requirement_id": "stock", "status": "unknown", "refinement_excluded": True}]}]}
    actions, pending, needs_candidates = gap_actions(answer, task, [])
    assert actions == {} and pending == set() and not needs_candidates


def test_hard_failure_prevents_direct_fetches_for_other_candidates():
    task, answer, sources = rejected_hotel()
    task.activity = "service"
    task.requirements.append(Requirement(id="price", kind="price", description="Published price"))
    payload = answer.model_dump()
    payload["findings"].append({"name": "Another venue", "source_url": "https://venue.fi/sauna",
        "checks": [{"requirement_id": "price", "status": "unknown"}]})
    actions, pending, needs_candidates = gap_actions(payload, task, sources)
    assert not actions and needs_candidates


def test_worker_does_not_stop_for_stall_before_untried_replacement(tmp_path, monkeypatch):
    from app import worker
    from app.orchestration.decisions import RouteDecision
    from app.orchestration.orchestrator import TurnResult
    from app.runs import RunStore
    from app.store import ChatStore

    monkeypatch.setenv("CHAT_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setattr(worker, "decide", lambda *args: RouteDecision(workflow="research", confidence=1))
    task, rejected, pages = rejected_hotel()
    calls = []

    def research(message, history, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            kwargs["attempted_queries"].add(query_key(SearchQuery(query="Tartu accommodation", objective="Discover")))
        elif len(calls) == 2:
            query = replacement_gap_queries(rejected.model_dump(), task, kwargs["attempted_queries"], pages)[0]
            kwargs["attempted_queries"].add(query_key(query))
        if len(calls) < 3:
            answer, sources = rejected, pages
        else:
            url = "https://hoteltartu.ee/"
            quote = "Hotel Tartu is a hotel located in Tartu."
            sources = [{"url": url, "title": "Hotel Tartu", "text": "# Hotel Tartu\n\n" + quote,
                        "retrieval": "live", "observed_at": datetime.now(UTC).isoformat()}]
            answer = GroundedAnswer(findings=[Finding(name="Tartu", source_url=url, checks=[RequirementCheck(
                requirement_id="hotel", requirement="Named hotel located in this destination", status="met",
                statement=quote, related_entity="Hotel Tartu", scope="related",
                evidence=[Evidence(source_url=url, quote=quote)])])])
        return TurnResult(reply="Evidence", sources=sources, debug={"output": answer.model_dump_json(),
            "research_contract": task.model_dump()})

    monkeypatch.setattr(worker, "run_turn", research)
    chats, runs = ChatStore(), RunStore()
    chat_id = chats.create("owner")["id"]
    run = runs.create("owner", chat_id, "replacement", "Find a hotel in Tartu", "deep")
    runs.claim()
    worker.execute(run["id"])
    saved = runs.get("owner", run["id"])
    assert len(calls) == 3, saved["debug"]
    assert saved["status"] == "completed", saved["debug"]
    assert saved["debug"]["stop_reason"] == "sufficient_evidence"
    assert saved["debug"]["task_state"]["answer"]["findings"][0]["checks"][0]["related_entity"] == "Hotel Tartu"


def test_worker_stops_partial_after_first_answer_when_only_stock_text_is_unavailable(tmp_path, monkeypatch):
    from app import worker
    from app.orchestration.decisions import RouteDecision
    from app.orchestration.orchestrator import TurnResult
    from app.runs import RunStore
    from app.store import ChatStore

    monkeypatch.setenv("CHAT_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setattr(worker, "decide", lambda *args: RouteDecision(workflow="research", confidence=1))
    task = ResearchContract(activity="product", requested_count=1,
        requirements=[Requirement(id="ram", description="16 GB RAM")])
    url, quote = "https://shop.fi/product/a", "Laptop A has 16 GB RAM."
    page = {"url": url, "title": "Laptop A", "text": quote, "retrieval": "live",
            "observed_at": datetime.now(UTC).isoformat()}
    answer = GroundedAnswer(findings=[Finding(name="Laptop A", source_url=url, source_kind="product", checks=[
        RequirementCheck(requirement_id="ram", requirement="16 GB RAM", status="met", statement="16 GB RAM",
                         evidence=[Evidence(source_url=url, quote=quote)])])])
    calls = []

    def research(*args, **kwargs):
        calls.append(1)
        return TurnResult(reply="Evidence", sources=[page], debug={"output": answer.model_dump_json(),
            "research_contract": task.model_dump()})

    monkeypatch.setattr(worker, "run_turn", research)
    chats, runs = ChatStore(), RunStore()
    chat_id = chats.create("owner")["id"]
    run = runs.create("owner", chat_id, "stock", "Find a laptop with 16 GB RAM", "auto")
    runs.claim()
    worker.execute(run["id"])
    saved = runs.get("owner", run["id"])
    assert calls == [1]
    assert saved["status"] == "partial"
    assert saved["debug"]["stop_reason"] == "unconfirmable_evidence"
    assert "To confirm" in saved["result"]["reply"]
