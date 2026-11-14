from types import SimpleNamespace

import pytest

from app.model_calls import call_model
from app.research_modes import RESEARCH_LIMITS, RESEARCH_MODES, research_mode, research_deadline, ResearchDeadline


def test_mode_limits_share_the_supervisor_profiles():
    from app import worker
    assert worker.RESEARCH_LIMITS is RESEARCH_LIMITS
    assert RESEARCH_LIMITS == {'fast': (1, 45), 'auto': (2, 120), 'deep': (3, 240)}
    assert [p.prompt_chars for p in RESEARCH_MODES.values()] == [150000, 320000, 600000]
    assert [p.livecrawl_pages for p in RESEARCH_MODES.values()] == [4, 8, 16]


def test_expired_run_deadline_prevents_another_model_request():
    token = research_deadline.set(0)
    try:
        with pytest.raises(ResearchDeadline):
            call_model(SimpleNamespace(run_sync=lambda *args, **kwargs: pytest.fail('Expired request')), 'test', stage='answering')
    finally:
        research_deadline.reset(token)


def test_fast_planner_uses_its_own_model_override(monkeypatch):
    from app.orchestration import planner
    from app.orchestration.contracts import ResearchContract, SearchPlan
    from pydantic_ai.usage import RunUsage
    monkeypatch.setenv('GEMINI_FAST_PLANNING_MODEL', 'fast-test-model')
    monkeypatch.setenv('GEMINI_PLANNING_MODEL', 'auto-test-model')
    monkeypatch.setattr(planner, 'api_key', lambda: 'fixture')
    monkeypatch.setattr(planner, 'google_provider', lambda *args: None)
    names = []
    monkeypatch.setattr(planner, 'GoogleModel', lambda name, **kwargs: names.append(name))
    monkeypatch.setattr(planner, 'Agent', lambda *args, **kwargs: object())
    monkeypatch.setattr(planner, 'call_model', lambda *args, **kwargs: SimpleNamespace(output=SearchPlan(contract=ResearchContract(), queries=[]), usage=RunUsage()))
    token = research_mode.set('fast')
    try:
        planner.plan_searches('Find venues')
    finally:
        research_mode.reset(token)
    assert names == ['fast-test-model']
