from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelAuthenticationError, ModelHTTPError, ModelQuotaExceededError, ModelRateLimitError

from app import model_calls, worker
from app.model_calls import call_model, error_details, model_call_events
from app.orchestration.decisions import DecisionUnavailable
from app.runs import RunStore
from app.store import ChatStore


def test_rate_limit_retries_with_provider_delay(monkeypatch):
    calls, waits, events = [], [], []

    def run(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ModelRateLimitError("fixture-model", "busy", retry_after=3)
        return "answer"

    monkeypatch.setattr(model_calls.time, "sleep", waits.append)
    token = model_call_events.set(events.append)
    try:
        assert call_model(SimpleNamespace(run_sync=run), "hello", stage="answering") == "answer"
    finally:
        model_call_events.reset(token)
    assert len(calls) == 2
    assert waits == [3]
    assert events[1]["status"] == "retrying"
    assert events[1]["retry_at"]
    assert events[-1]["status"] == "completed"


@pytest.mark.parametrize("error", [
    ModelRateLimitError("fixture-model", "busy", retry_after=60),
    ModelQuotaExceededError("fixture-model", "daily quota"),
    ModelAuthenticationError("fixture-model", "bad key"),
])
def test_nonrecoverable_or_long_wait_does_not_retry(monkeypatch, error):
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        raise error

    monkeypatch.setattr(model_calls.time, "sleep", lambda _: pytest.fail("Unexpected wait"))
    with pytest.raises(type(error)):
        call_model(SimpleNamespace(run_sync=run), "hello", stage="routing")
    assert len(calls) == 1


def test_retry_attempts_are_bounded(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        raise ModelRateLimitError("fixture-model", "busy", retry_after=0)

    monkeypatch.setattr(model_calls.time, "sleep", lambda _: None)
    with pytest.raises(ModelRateLimitError):
        call_model(SimpleNamespace(run_sync=run), "hello", stage="planning")
    assert len(calls) == 3


def test_provider_details_redact_configured_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-fixture-key")
    details = error_details(ModelRateLimitError("fixture-model", "key private-fixture-key rejected", body={"key": "private-fixture-key"}))
    assert "private-fixture-key" not in str(details)
    assert details["code"] == "rate_limited"
    assert details["retryable"]


def test_http_402_misclassified_by_sdk_is_not_retried(monkeypatch):
    error = ModelHTTPError.for_category(
        ModelRateLimitError, status_code=402, model_name="fixture-model",
        body={"error": {"code": 402, "status": "RESOURCE_EXHAUSTED", "message": "Your prepayment credits are depleted."}},
    )
    details = error_details(error)
    assert details["code"] == "billing_required"
    assert not details["retryable"]
    assert "prepaid credits are depleted" in details["message"]
    monkeypatch.setattr(model_calls.time, "sleep", lambda _: pytest.fail("Billing failures must not be retried"))
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        raise error

    with pytest.raises(ModelRateLimitError):
        call_model(SimpleNamespace(run_sync=run), "hello", stage="answering")
    assert len(calls) == 1


def test_worker_stops_after_router_rate_limit_and_saves_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_DATABASE_PATH", str(tmp_path / "rates.sqlite3"))
    monkeypatch.setattr(worker, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(worker, "decide", lambda *args: (_ for _ in ()).throw(DecisionUnavailable("fixture")))
    monkeypatch.setattr(worker, "Agent", lambda *args, **kwargs: SimpleNamespace(run_sync=lambda *args, **kwargs: (_ for _ in ()).throw(ModelRateLimitError("fixture-model", "minute limit", retry_after=60))))
    monkeypatch.setattr(worker, "run_turn", lambda *args, **kwargs: pytest.fail("Must not start research while Gemini is rate limited"))
    chats, runs = ChatStore(), RunStore()
    chat = chats.create("owner")
    run = runs.create("owner", chat["id"], "request", "Find five computers", "auto")
    runs.claim()
    worker.execute(run["id"])
    saved = runs.get("owner", run["id"])
    assert saved["status"] == "failed"
    assert saved["phase"] == "rate_limited"
    assert saved["debug"]["error"]["retry_after"] == 60
    assert "60 seconds" in saved["debug"]["error"]["message"]
    assert saved["debug"]["operation"]["stage"] == "routing"
    assert model_call_events.get() is None
