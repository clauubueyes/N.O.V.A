from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: str = Field(description='Either "system", "user" or "assistant"')
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ChatResponse(BaseModel):
    id: str
    model: str
    message: dict[str, str]
    usage: dict | None = None


class ModelInfoOut(BaseModel):
    name: str
    size: int = 0
    modified_at: str = ""


class ToolInfoOut(BaseModel):
    name: str
    description: str


class SessionCreateResponse(BaseModel):
    session_id: str
    model: str
    agent: str = ""


class SessionCreateRequest(BaseModel):
    agent: str | None = Field(default=None, description="Bind the session to an agent preset")


class SessionChatRequest(BaseModel):
    message: str
    model: str | None = None


class SessionChatResponse(BaseModel):
    session_id: str
    reply: str
    model: str
    context: str = ""
    agent: str = ""
    steps: list[dict] = Field(default_factory=list)


class AgentInfoOut(BaseModel):
    name: str
    description: str


class AgentChatRequest(BaseModel):
    message: str
    model: str | None = None


class AgentChatResponse(BaseModel):
    agent: str
    reply: str
    model: str
    steps: list[dict] = Field(default_factory=list)


class SessionRunRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class RememberRequest(BaseModel):
    content: str
    kind: str = "fact"
    source: str = "user"