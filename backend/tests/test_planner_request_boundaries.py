from datetime import UTC, datetime
from types import SimpleNamespace

from pydantic_ai.usage import RunUsage

from app.orchestration import planner
from app.orchestration.contracts import ResearchContract, SearchPlan, SearchQuery


def model_result(monkeypatch, contract, queries=None):
    output = SearchPlan(contract=contract, queries=queries or [])
    monkeypatch.setattr(planner, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(planner, "google_provider", lambda *args: None)
    monkeypatch.setattr(planner, "GoogleModel", lambda *args, **kwargs: object())
    monkeypatch.setattr(planner, "Agent", lambda *args, **kwargs: object())
    monkeypatch.setattr(planner, "datetime", SimpleNamespace(now=lambda *args: datetime(2026, 11, 14, tzinfo=UTC)))
    monkeypatch.setattr(planner, "call_model", lambda *args, **kwargs: SimpleNamespace(output=output, usage=RunUsage()))


def test_injected_reference_date_cannot_become_requested_result_count(monkeypatch):
    model_result(monkeypatch, ResearchContract(requested_count=14, count_quote="14\nFind hotels"))
    result, debug = planner.plan_searches("Find hotels")
    assert "Current UTC date: 2026-11-14" in debug["input"]
    assert result.contract.requested_count is None
    assert result.contract.count_quote is None


def test_previous_context_count_cannot_authorize_current_count(monkeypatch):
    model_result(monkeypatch, ResearchContract(requested_count=5, count_quote="5 hotels"))
    result, _ = planner.plan_searches("Find restaurants\nRecent context; earlier request:\nFind 5 hotels")
    assert result.contract.requested_count is None


def test_explicit_current_count_wins_over_date_and_older_count(monkeypatch):
    model_result(monkeypatch, ResearchContract(requested_count=14, count_quote="14\nFind 3 hotels"))
    result, _ = planner.plan_searches("Find 3 hotels\nRecent context; earlier request:\nFind 5 hotels")
    assert result.contract.requested_count == 3
    assert result.contract.count_quote == "3 hotels"


def test_injected_date_does_not_authorize_publication_filter(monkeypatch):
    quote = "Current UTC date: 2026-11-14\nFind news"
    model_result(monkeypatch, ResearchContract(), [SearchQuery(query="news", objective="Find news", filter_quote=quote,
        category="news", start_published_date="2026-11-14")])
    result, _ = planner.plan_searches("Find news")
    assert result.queries[0].start_published_date is None
    assert result.queries[0].category is None


def test_refinement_requests_only_queries_with_minimal_thinking(monkeypatch):
    task = ResearchContract(original_request="Find hotels")
    model_result(monkeypatch, task)
    requested = {}
    monkeypatch.setattr(planner, 'Agent', lambda *args, **kwargs: requested.update(kwargs) or object())
    def call(*args, **kwargs):
        requested.update(kwargs)
        return SimpleNamespace(output=planner.RefinementPlan(queries=[]), usage=RunUsage())
    monkeypatch.setattr(planner, 'call_model', call)
    plan, _ = planner.plan_searches('Resolve Hotel A location', task)
    assert requested['output_type'] is planner.RefinementPlan
    assert requested['model_settings']['thinking'] == 'minimal'
    assert requested['model_settings']['max_tokens'] <= 1200
    assert plan.contract is task
