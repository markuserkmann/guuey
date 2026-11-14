import os
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query

from app.auth import require_api_key
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
)
from app.store import ChatStore

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
        auth_configured=len(os.getenv("AGENT_API_TOKEN", "")) >= 32,
        exa_configured=bool(os.getenv("EXA_API_KEY")),
    )


@app.get("/api/chats", response_model=list[ChatSummary])
def list_chats(query: str = Query(default="", max_length=100), owner: str = Owner):
    return get_store().list(owner, query)


@app.post("/api/chats", response_model=ChatSummary, status_code=201)
def create_chat(owner: str = Owner):
    return get_store().create(owner)


@app.get("/api/chats/{chat_id}", response_model=ChatDetail)
def read_chat(chat_id: str, owner: str = Owner):
    chat = get_store().detail(owner, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@app.post("/api/chats/{chat_id}/messages", response_model=SendMessageResponse)
def send_message(chat_id: str, body: SendMessageRequest, owner: str = Owner):
    chat = get_store().detail(owner, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    history = [ChatTurn.model_validate(message) for message in chat["messages"][-12:]]
    try:
        result = run_turn(body.message, history, search_requested=body.search)
    except (ModelUnavailable, SearchUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The assistant could not complete this turn.") from exc

    exchange = get_store().append_exchange(
        owner, chat_id, body.message, result.reply, result.sources
    )
    if exchange is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {**exchange, "debug": result.debug}
