from types import SimpleNamespace

import pytest
from pydantic_ai.usage import RunUsage

from app import chat
from app.model_selection import DEFAULT_MODEL, STAGE_VARIABLES, base_model_name, stage_model_name
from app.orchestration import planner
from app.orchestration.contracts import ResearchContract, SearchPlan


@pytest.fixture(autouse=True)
def clear_model_environment(monkeypatch):
    for name in ["GEMINI_MODEL", *STAGE_VARIABLES.values()]:
        monkeypatch.delenv(name, raising=False)


def test_all_stages_keep_existing_default_without_configuration():
    assert base_model_name() == DEFAULT_MODEL == "gemini-3.5-flash-lite"
    assert {stage_model_name(stage) for stage in STAGE_VARIABLES} == {DEFAULT_MODEL}


@pytest.mark.parametrize("stage", list(STAGE_VARIABLES))
def test_stage_override_is_independent_and_preserves_chat(stage, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    monkeypatch.setenv(STAGE_VARIABLES[stage], " selected-stage-model ")
    assert stage_model_name(stage) == "selected-stage-model"
    assert {stage_model_name(other) for other in STAGE_VARIABLES if other != stage} == {"base-model"}
    assert chat.model_name() == "base-model"


@pytest.mark.parametrize("blank", ["", " ", "\n"])
def test_blank_compose_overrides_fall_back_to_base(blank, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    for variable in STAGE_VARIABLES.values():
        monkeypatch.setenv(variable, blank)
    assert {stage_model_name(stage) for stage in STAGE_VARIABLES} == {"base-model"}
    monkeypatch.setenv("GEMINI_MODEL", blank)
    assert {stage_model_name(stage) for stage in STAGE_VARIABLES} == {DEFAULT_MODEL}


def test_planner_uses_selected_model_and_records_actual_model(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    monkeypatch.setenv("GEMINI_PLANNING_MODEL", "planner-model")
    monkeypatch.setenv("GEMINI_RESEARCH_MODEL", "research-model")
    monkeypatch.setenv("GEMINI_VERIFICATION_MODEL", "verification-model")
    monkeypatch.setattr(planner, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(planner, "google_provider", lambda key: None)
    constructed = []
    monkeypatch.setattr(planner, "GoogleModel", lambda name, **kwargs: constructed.append(name) or name)
    monkeypatch.setattr(planner, "Agent", lambda *args, **kwargs: object())
    plan = SearchPlan(contract=ResearchContract(), queries=[])
    monkeypatch.setattr(planner, "call_model", lambda *args, **kwargs: SimpleNamespace(output=plan, usage=RunUsage()))
    _, debug = planner.plan_searches("Find a sauna")
    assert constructed == ["planner-model"]
    assert debug["model"] == "planner-model"
