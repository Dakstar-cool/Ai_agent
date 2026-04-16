from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    kind: str = "interaction"
    session_id: str | None = None
    user_message: str = ""
    assistant_reply: str = ""
    route: str = "general"
    metadata: dict[str, Any] = Field(default_factory=dict)
    project_path: str | None = None
    created_at: str | None = None


class MemoryRecallQuery(BaseModel):
    text: str
    session_id: str | None = None
    project_path: str | None = None
    route: str | None = None
    limit: int | None = None


class MemoryRecallItem(BaseModel):
    summary: str
    score: int
    route: str = "general"
    session_id: str | None = None
    project_path: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
