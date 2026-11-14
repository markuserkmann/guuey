import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRequestTimeout, ModelTimeoutError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from app import model_calls


def test_deadline_cancels_actual_sdk_request_without_retry():
    calls = []

    async def stalled_model(messages, info):
        calls.append(1)
        await asyncio.sleep(10)

    events = []
    token = model_calls.model_call_events.set(events.append)
    try:
        with pytest.raises(ModelRequestTimeout):
            model_calls.call_model(Agent(FunctionModel(stalled_model)), "hello", stage="planning",
                                   model_settings={"request_timeout": 0.02})
    finally:
        model_calls.model_call_events.reset(token)
    assert calls == [1]
    assert events[-1]["status"] == "failed"
    assert events[-1]["error"]["code"] == "model_timeout"
    assert events[-1]["elapsed_seconds"] < 2


def test_transport_timeout_is_not_retried(monkeypatch):
    calls = []

    def stalled_transport(*args, **kwargs):
        calls.append(1)
        raise ModelTimeoutError("test", "timed out")

    monkeypatch.setattr(model_calls.time, "sleep", lambda _: pytest.fail("Timeout must not retry"))
    with pytest.raises(ModelTimeoutError):
        model_calls.call_model(SimpleNamespace(run_sync=stalled_transport), "hello", stage="answering")
    assert calls == [1]


def test_deadline_cancels_stalled_stream():
    async def stalled_stream(messages, info):
        await asyncio.sleep(10)
        yield "late output"

    with pytest.raises(ModelRequestTimeout):
        model_calls.call_model(Agent(FunctionModel(stream_function=stalled_stream)), "hello", stage="answering",
                               model_settings={"request_timeout": 0.02}, on_output=lambda output: None)


def test_stage_deadline_preserves_caller_settings_and_records_latency():
    settings = {"max_tokens": 256}
    events, sent = [], []
    result = SimpleNamespace(output="done", usage=RunUsage())

    def run(*args, **kwargs):
        sent.append(kwargs["model_settings"].copy())
        return result

    token = model_calls.model_call_events.set(events.append)
    try:
        assert model_calls.call_model(SimpleNamespace(run_sync=run), "hello", stage="routing", model_settings=settings) is result
    finally:
        model_calls.model_call_events.reset(token)
    assert settings == {"max_tokens": 256}
    assert 19 < sent[0]["request_timeout"] <= 20
    assert sent[0]["max_tokens"] == 256
    assert events[-1]["elapsed_seconds"] >= 0
    assert events[-1]["attempt_seconds"] >= 0
