from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    mode: str
    version: str = "0.1.0"
    model: str
    auth_configured: bool


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str
