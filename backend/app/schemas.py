from typing import Literal

from pydantic import BaseModel, Field

from app.attachments import Attachments


class HealthResponse(BaseModel):
    status: str
    mode: str
    version: str = "0.1.0"
    model: str
    auth_configured: bool
    exa_configured: bool
    browser_enabled: bool = False
    saved_logins_enabled: bool = False


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    attachments: Attachments = Field(default_factory=list)


class ChatSource(BaseModel):
    title: str
    url: str


class ChatSummary(BaseModel):
    id: str
    title: str
    updated_at: str
    pinned: bool = False


class UpdateChatRequest(BaseModel):
    pinned: bool


class StoredMessage(ChatTurn):
    id: str
    sources: list[ChatSource] = Field(default_factory=list)
    created_at: str


class ChatDetail(ChatSummary):
    messages: list[StoredMessage]


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    search: bool = False
    attachments: Attachments = Field(default_factory=list)


class SendMessageResponse(BaseModel):
    user: StoredMessage
    assistant: StoredMessage
    debug: dict
