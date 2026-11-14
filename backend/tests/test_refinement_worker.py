from datetime import UTC, datetime

import pytest

from app import worker
from app.orchestration.contracts import Requirement, ResearchContract, SearchQuery
from app.orchestration.decisions import RouteDecision
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck
from app.orchestration.interpretation import query_key
from app.orchestration.orchestrator import TurnResult
from app.runs import RunStore
from app.store import ChatStore


REQUEST = "Recommend Madeira with a named hotel located there."
DESTINATION = "https://madeira.example/"
HOTEL = "https://hotel.example/location"


def contract():
    return ResearchContract(activity="research", requested_count=1, requirements=[
        Requirement(id="destination", description="Madeira has walking trails"),
        Requirement(id="hotel", description="Named hotel located in Madeira", scope="related"),
    ])


def sources():
    observed = datetime.now(UTC).isoformat()
    return [
        {"url": DESTINATION, "title": "Madeira", "text": "Madeira has walking trails.", "retrieval": "live", "retrieved_at": observed},
        {"url": HOTEL, "title": "Hotel A", "text": "Hotel A is located in Madeira.", "retrieval": "live", "retrieved_at": observed},
    ]


def partial_answer(related=False):
    requirement_id, statement, url = ("hotel", "Hotel A is located in Madeira.", HOTEL) if related else (
        "destination", "Madeira has walking trails.", DESTINATION)
    check = RequirementCheck(requirement_id=requirement_id, requirement=requirement_id, status="met",
        statement=statement, related_entity="Hotel A" if related else None, scope="related" if related else "primary",
        evidence=[Evidence(source_url=url, quote=statement)])
    return GroundedAnswer(findings=[Finding(name="Madeira", source_url=DESTINATION, source_kind="other", checks=[check])])


def stores(tmp_path, monkeypatch):
    path = str(tmp_path / "worker.sqlite")
    monkeypatch.setenv("CHAT_DATABASE_PATH", path)
    monkeypatch.setattr(worker, "decide", lambda *args: RouteDecision(workflow="research", confidence=1))
    chats, runs = ChatStore(path), RunStore(path)
    return chats.create("owner")["id"], runs


def result(related, research_contract=None):
    return TurnResult(reply="fixture partial", sources=sources(), debug={
        "research_contract": (research_contract or contract()).model_dump(),
        "output": partial_answer(related).model_dump_json(), "has_gaps": True,
    })


def test_worker_persists_complete_merge_of_complementary_rounds(tmp_path, monkeypatch):
    chat, runs = stores(tmp_path, monkeypatch)
    calls = []

    def research(message, history, **kwargs):
        calls.append(kwargs)
        query = SearchQuery(query="hotel location" if len(calls) == 2 else "Madeira trails", objective="Verify requirement")
        kwargs["attempted_queries"].add(query_key(query))
        return result(related=len(calls) == 2, research_contract=kwargs["contract"])

    monkeypatch.setattr(worker, "run_turn", research)
    run = runs.create("owner", chat, "initial", REQUEST, "auto")
    runs.claim()
    worker.execute(run["id"])
    saved = runs.get("owner", run["id"])
    assert saved["status"] == "completed", saved["debug"]
    assert len(calls) == 2
    assert calls[1]["prior_answer"]["findings"][0]["name"] == "Madeira"
    assert saved["debug"]["stages"][0]["coverage"]["verified"] == 0
    assert saved["debug"]["stages"][1]["coverage"]["verified"] == 1
    assert saved["debug"]["stages"][1]["refinement"]["carried_checks"] == 1
    answer = saved["debug"]["task_state"]["answer"]
    assert {check["requirement_id"]: check["status"] for check in answer["findings"][0]["checks"]} == {
        "destination": "met", "hotel": "met"}
    assert DESTINATION in saved["result"]["reply"] and HOTEL in saved["result"]["reply"]
    assert len(saved["debug"]["task_state"]["attempted_queries"]) == 2


def test_worker_restores_attempted_queries_and_answer_on_continuation(tmp_path, monkeypatch):
    chat, runs = stores(tmp_path, monkeypatch)
    initial_calls = []

    def initial_research(message, history, **kwargs):
        key = query_key(SearchQuery(query=f"Madeira trails source {len(initial_calls)}", objective="Verify trails"))
        initial_calls.append(key)
        kwargs["attempted_queries"].add(key)
        return result(related=False, research_contract=kwargs["contract"])

    monkeypatch.setattr(worker, "run_turn", initial_research)
    parent = runs.create("owner", chat, "initial", REQUEST, "auto", timezone="Europe/Helsinki")
    runs.claim()
    worker.execute(parent["id"])
    before = runs.get("owner", parent["id"])
    assert before["status"] == "partial", before["debug"]
    captured = []

    def continuation_research(message, history, **kwargs):
        captured.append({"attempted": set(kwargs["attempted_queries"]), "contract": kwargs["contract"].model_dump(),
                         "answer": kwargs["prior_answer"]})
        kwargs["attempted_queries"].add(query_key(SearchQuery(query="Hotel A Madeira location", objective="Verify hotel")))
        return result(related=True, research_contract=kwargs["contract"])

    monkeypatch.setattr(worker, "run_turn", continuation_research)
    child = runs.create("owner", chat, "continue:" + parent["id"], REQUEST, "deep", parent["id"], timezone="Europe/Helsinki")
    runs.claim()
    worker.execute(child["id"])
    saved = runs.get("owner", child["id"])
    assert saved["status"] == "completed", saved["debug"]
    assert len(captured) == 1
    assert captured[0]["attempted"] == set(initial_calls)
    assert captured[0]["contract"] == before["debug"]["task_state"]["contract"]
    assert captured[0]["answer"] == before["debug"]["task_state"]["answer"]
    assert set(initial_calls).issubset(saved["debug"]["task_state"]["attempted_queries"])
    assert saved["debug"]["stages"][0]["coverage"]["verified"] == 1


@pytest.mark.parametrize('mode,expected_calls,expected_status,stop_reason', [
    ('auto', 2, 'partial', 'round_limit'),
    ('deep', 3, 'completed', 'sufficient_evidence'),
])
def test_existing_pages_can_add_facts_within_mode_limits(tmp_path, monkeypatch, mode, expected_calls, expected_status, stop_reason):
    chat, runs = stores(tmp_path, monkeypatch)
    checks = [Requirement(id=f"fact_{i}", description=f"Madeira fact {i}") for i in range(3)]
    task = ResearchContract(activity="research", requested_count=1, requirements=checks)
    page = {"url": DESTINATION, "title": "Madeira", "retrieval": "page",
            "text": "\n\n".join(f"Madeira fact {i}." for i in range(3))}
    calls = []

    def research(message, history, **kwargs):
        index = len(calls)
        calls.append(index)
        statement = f"Madeira fact {index}."
        finding = Finding(name="Madeira", source_url=DESTINATION, checks=[RequirementCheck(
            requirement_id=f"fact_{index}", requirement=statement, status="met", statement=statement,
            evidence=[Evidence(source_url=DESTINATION, quote=statement)])])
        return TurnResult(reply="partial", sources=[page], debug={
            "research_contract": task.model_dump(), "output": GroundedAnswer(findings=[finding]).model_dump_json()})

    monkeypatch.setattr(worker, "run_turn", research)
    run = runs.create("owner", chat, "progress", REQUEST, mode)
    runs.claim()
    worker.execute(run["id"])
    saved = runs.get("owner", run["id"])
    assert saved["status"] == expected_status, saved["debug"]
    assert len(calls) == expected_calls
    assert saved["debug"]["stop_reason"] == stop_reason
    assert saved["debug"]["stages"][-1]["coverage"]["supported_checks"] == expected_calls


def test_deadline_stops_before_starting_a_round_that_will_not_fit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    chat, runs = stores(tmp_path, monkeypatch)
    calls = []
    def research(*args, **kwargs):
        calls.append(1)
        clock[0] += 60
        return result(False)
    monkeypatch.setattr(worker, 'run_turn', research)
    run = runs.create('owner', chat, 'deadline', REQUEST, 'auto')
    runs.claim()
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert len(calls) == 1
    assert saved['status'] == 'partial'
    assert saved['debug']['stop_reason'] == 'deadline'
    assert saved['debug']['first_answer_s'] == 60
    assert saved['result']['reply']


def test_new_urls_do_not_reset_evidence_stall(tmp_path, monkeypatch):
    chat, runs = stores(tmp_path, monkeypatch)
    calls = []
    def research(*args, **kwargs):
        calls.append(1)
        turn = result(False)
        turn.sources.append({'url': f'https://other.example/{len(calls)}', 'title': 'Unrelated', 'text': 'No useful facts.'})
        return turn
    monkeypatch.setattr(worker, 'run_turn', research)
    run = runs.create('owner', chat, 'stall', REQUEST, 'deep')
    runs.claim()
    worker.execute(run['id'])
    assert len(calls) == 2
    assert runs.get('owner', run['id'])['debug']['stop_reason'] == 'no_evidence_progress'
