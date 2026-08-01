from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.providers.memory.models import MemoryExportItem, MemoryScopeQuery


class MemoryScopeRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    user_id: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_scope(self) -> MemoryScopeRequest:
        if not any((self.user_id, self.project_id, self.session_id)):
            raise ValueError("A user, project, or session scope is required")
        return self

    def to_query(self) -> MemoryScopeQuery:
        return MemoryScopeQuery(
            user_id=self.user_id,
            project_id=self.project_id,
            session_id=self.session_id,
        )


class MemoryExportResponse(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    items: list[MemoryExportItem]


class MemoryDeleteResponse(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    deleted: int = Field(ge=0)
