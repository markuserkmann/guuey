from fastapi import Depends, FastAPI, HTTPException
import os

from app.auth import require_api_key
from app.chat import api_key, generate_reply, model_name
from app.schemas import ChatRequest, ChatResponse, HealthResponse

app = FastAPI(title="Guuey API")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    configured = api_key() is not None
    return HealthResponse(
        status="ok",
        mode="gemini" if configured else "mock",
        model=model_name() if configured else "local mock",
        auth_configured=len(os.getenv("AGENT_API_TOKEN", "")) >= 32,
    )


@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
def chat(body: ChatRequest) -> ChatResponse:
    try:
        return ChatResponse(reply=generate_reply(body.message, body.history))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Gemini request failed") from exc
