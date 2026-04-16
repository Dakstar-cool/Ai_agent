from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    project_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionStep(BaseModel):
    name: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    route: str
    reply: str
    steps: list[ExecutionStep] = Field(default_factory=list)
