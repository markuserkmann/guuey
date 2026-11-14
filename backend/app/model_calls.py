import json
import math
import os
import random
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Callable
from types import SimpleNamespace

from google.genai.types import HttpRetryOptions
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelServerError,
    ModelUnavailableError,
)
from pydantic_ai.providers.google import GoogleProvider

model_call_events: ContextVar[Callable[[dict], None] | None] = ContextVar("model_call_events", default=None)
model_output_events: ContextVar[Callable[[dict], None] | None] = ContextVar("model_output_events", default=None)
TRANSIENT_ERRORS = (ModelRateLimitError, ModelConnectionError, ModelServerError, ModelUnavailableError)


def google_provider(key: str) -> GoogleProvider:
    # The application owns retries so waits are bounded and visible in the run.
    return GoogleProvider(api_key=key, retry_options=HttpRetryOptions(attempts=1))


def error_details(exc: Exception) -> dict:
    raw = str(exc)
    body = json.dumps(getattr(exc, "body", None), default=str, ensure_ascii=False)
    status_code = getattr(exc, "status_code", None)
    depleted = "prepayment credits are depleted" in (raw + body).lower()
    billing_failure = status_code == 402 or depleted
    retry_after = getattr(exc, "retry_after", None)
    if not isinstance(retry_after, (int, float)) or not math.isfinite(retry_after) or retry_after < 0:
        retry_after = None
    if billing_failure:
        code = "billing_required"
        message = "Gemini prepaid credits are depleted. Add credits in Google AI Studio, then try again." if depleted else "Gemini rejected the request because of billing. Check the project's billing in Google AI Studio, then try again."
    elif isinstance(exc, ModelQuotaExceededError):
        code, message = "quota_exceeded", "Gemini quota is exhausted. Check the project's usage and billing in Google AI Studio before retrying."
    elif isinstance(exc, ModelRateLimitError):
        code, message = "rate_limited", "Gemini is temporarily rate-limiting requests."
        message += f" Try again after {math.ceil(retry_after)} seconds." if retry_after else " Wait a moment and try again."
    elif isinstance(exc, ModelAuthenticationError):
        code, message = "authentication_failed", "Gemini rejected the API key. Check the backend Gemini key configuration."
    elif isinstance(exc, TRANSIENT_ERRORS):
        code, message = "temporarily_unavailable", "Gemini is temporarily unavailable. Please try again shortly."
    else:
        code, message = "provider_error", "The provider could not complete this request. See Debug for the error details."
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "EXA_API_KEY", "AGENT_API_TOKEN"):
        secret = os.getenv(name)
        if secret:
            raw, body = raw.replace(secret, "[redacted]"), body.replace(secret, "[redacted]")
    return {
        "code": code, "message": message, "type": type(exc).__name__,
        "provider": "gemini" if isinstance(exc, ModelAPIError) else None,
        "model": getattr(exc, "model_name", None), "status_code": status_code,
        "retryable": not billing_failure and isinstance(exc, TRANSIENT_ERRORS), "retry_after": retry_after,
        "detail": raw[:2000], "body": body[:4000],
    }


def call_model(agent, prompt: str, *, stage: str, model_settings: dict | None = None, on_output: Callable | None = None):
    waited = 0.0
    for attempt in range(1, 4):
        observer = model_call_events.get()
        if observer:
            observer({"stage": stage, "attempt": attempt, "status": "started"})
        try:
            if on_output:
                with agent.run_stream_sync(prompt, model_settings=model_settings) as stream:
                    for output in stream.stream_output(debounce_by=0.15):
                        on_output(output)
                    result = SimpleNamespace(output=stream.get_output(), usage=stream.usage)
            else:
                result = agent.run_sync(prompt, model_settings=model_settings)
            if observer:
                observer({"stage": stage, "attempt": attempt, "status": "completed"})
            return result
        except ModelAPIError as exc:
            details = error_details(exc)
            delay = details["retry_after"]
            if delay is None:
                delay = 2 ** attempt + random.uniform(0, 0.5)
            retry = details["retryable"] and attempt < 3 and waited + delay <= 30
            if observer:
                observer({
                    "stage": stage, "attempt": attempt,
                    "status": "retrying" if retry else "failed", "error": details,
                    "delay_seconds": delay if retry else None,
                    "retry_at": (datetime.now(UTC) + timedelta(seconds=delay)).isoformat() if retry else None,
                })
            if not retry:
                raise
            time.sleep(delay)
            waited += delay
