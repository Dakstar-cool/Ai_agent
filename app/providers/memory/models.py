from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str = "interaction_summary"
    session_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    summary: str = Field(default="", max_length=4_000)
    user_message: str = ""
    assistant_reply: str = ""
    route: str = "general"
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)
    project_path: str | None = None
    created_at: str | None = None
    expires_at: str | None = None


class MemoryRecallQuery(BaseModel):
    text: str
    session_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    project_path: str | None = None
    route: str | None = None
    limit: int | None = None


class MemoryRecallItem(BaseModel):
    id: str | None = None
    summary: str
    score: int
    kind: str = "interaction_summary"
    route: str = "general"
    session_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    project_path: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str | None = None


class MemoryScopeQuery(BaseModel):
    user_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None

    @model_validator(mode="after")
    def require_scope(self) -> MemoryScopeQuery:
        if not any((self.user_id, self.project_id, self.session_id)):
            raise ValueError("A user, project, or session scope is required")
        return self


class MemoryExportItem(BaseModel):
    id: str
    kind: str
    summary: str
    route: str
    session_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    created_at: str
    expires_at: str | None = None
