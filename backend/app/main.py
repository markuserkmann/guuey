import os
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.auth import require_api_key, valid_api_token
from app.attachments import Attachments, attachment_context
from app.chat import ModelUnavailable, api_key, model_name
from app.orchestration.orchestrator import run_turn
from app.research import SearchUnavailable
from app.schemas import (
    ChatDetail,
    ChatSummary,
    ChatTurn,
    HealthResponse,
    SendMessageRequest,
    SendMessageResponse,
    UpdateChatRequest,
)
from app.store import ChatStore
from app.streaming import stream_run

app = FastAPI(title="Guuey API")
Owner = Depends(require_api_key)


@lru_cache(maxsize=1)
def get_store() -> ChatStore:
    return ChatStore()


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    configured = api_key() is not None
    return HealthResponse(
        status="ok",
        mode="gemini" if configured else "mock",
        model=model_name() if configured else "local mock",
        auth_configured=valid_api_token(os.getenv("AGENT_API_TOKEN", "")),
        exa_configured=bool(os.getenv("EXA_API_KEY")),
    )


@app.get("/api/chats", response_model=list[ChatSummary])
def list_chats(query: str = Query(default="", max_length=100), owner: str = Owner):
    return get_store().list(owner, query)


@app.get("/api/session")
def session(owner: str = Owner):
    return {"authenticated": True}


@app.post("/api/chats", response_model=ChatSummary, status_code=201)
def create_chat(owner: str = Owner):
    return get_store().create(owner)


@app.get("/api/chats/{chat_id}", response_model=ChatDetail)
def read_chat(chat_id: str, owner: str = Owner):
    chat = get_store().detail(owner, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@app.patch("/api/chats/{chat_id}", response_model=ChatSummary)
def update_chat(chat_id: str, body: UpdateChatRequest, owner: str = Owner):
    chat = get_store().pin(owner, chat_id, body.pinned)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


@app.post("/api/chats/{chat_id}/messages", response_model=SendMessageResponse)
def send_message(chat_id: str, body: SendMessageRequest, owner: str = Owner):
    chat = get_store().detail(owner, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    history = [ChatTurn.model_validate(message) for message in chat["messages"][-12:]]
    try:
        result = run_turn(attachment_context(body.message, body.attachments), history, search_requested=body.search)
    except (ModelUnavailable, SearchUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The assistant could not complete this turn.") from exc

    exchange = get_store().append_exchange(
        owner, chat_id, body.message, result.reply, result.sources,
        attachments=[file.model_dump() for file in body.attachments],
    )
    if exchange is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {**exchange, "debug": result.debug}


from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field
from app.runs import RunStore


class CreateRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    mode: Literal["auto", "fast", "deep"] = "auto"
    request_id: UUID
    attachments: Attachments = Field(default_factory=list)


@lru_cache(maxsize=1)
def get_runs():
    return RunStore()


def owned_run(owner, run_id):
    run = get_runs().get(owner, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.post("/api/chats/{chat_id}/runs", status_code=202)
def create_run(chat_id: str, body: CreateRunRequest, owner: str = Owner):
    try:
        return get_runs().create(owner, chat_id, str(body.request_id), body.message, body.mode,
                                 attachments=[file.model_dump() for file in body.attachments])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/chats/{chat_id}/runs/latest")
def latest_run(chat_id: str, owner: str = Owner):
    if get_store().detail(owner, chat_id) is None:
        raise HTTPException(404, "Chat not found")
    return get_runs().latest(owner, chat_id)


@app.get("/api/runs/{run_id}")
def read_run(run_id: str, owner: str = Owner):
    return owned_run(owner, run_id)


@app.get("/api/runs/{run_id}/debug")
def run_debug(run_id: str, owner: str = Owner):
    return owned_run(owner, run_id)["debug"]


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, request: Request, owner: str = Owner):
    owned_run(owner, run_id)
    return StreamingResponse(stream_run(get_runs(), owner, run_id, request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str, owner: str = Owner):
    owned_run(owner, run_id)
    get_runs().finish(run_id, "cancelled", "cancelled")
    return owned_run(owner, run_id)


@app.post("/api/runs/{run_id}/continue", status_code=202)
def continue_run(run_id: str, owner: str = Owner):
    run = owned_run(owner, run_id)
    try:
        return get_runs().create(owner, run["chat_id"], f"continue:{run_id}", run["message"], "deep", run_id,
                                attachments=run["attachments"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
