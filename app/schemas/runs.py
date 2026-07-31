from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.runs.models import RunEventRecord, RunRecord, RunState, WorkspaceRecord
from app.schemas.chat import MAX_MESSAGE_LENGTH, MAX_METADATA_BYTES


class CreateRunRequest(BaseModel):
    schema_version: Literal["0.1.0"]
    workspace_id: UUID
    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metadata_size(self) -> "CreateRunRequest":
        size = len(json.dumps(self.metadata, ensure_ascii=False).encode("utf-8"))
        if size > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be at most {MAX_METADATA_BYTES} bytes")
        return self


class RunResponse(BaseModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    id: UUID
    workspace_id: UUID
    session_id: UUID
    state: RunState
    created_at: datetime
    updated_at: datetime
    result: str | None = None
    error: dict[str, Any] | None = None

    @classmethod
    def from_record(cls, record: RunRecord) -> "RunResponse":
        return cls(
            id=record.id,
            workspace_id=record.workspace_id,
            session_id=record.session_id,
            state=record.state,
            created_at=record.created_at,
            updated_at=record.updated_at,
            result=record.result,
            error=record.error,
        )


class RunEventResponse(BaseModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    type: str
    created_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_record(cls, record: RunEventRecord) -> "RunEventResponse":
        return cls(
            id=record.id,
            run_id=record.run_id,
            sequence=record.sequence,
            type=record.type,
            created_at=record.created_at,
            payload=record.payload,
        )


class RegisterWorkspaceRequest(BaseModel):
    schema_version: Literal["0.1.0"]
    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1, max_length=1_000)


class WorkspaceResponse(BaseModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    id: UUID
    name: str
    root_path: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: WorkspaceRecord) -> "WorkspaceResponse":
        return cls(
            id=record.id,
            name=record.name,
            root_path=record.root_path,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ApprovalDecisionRequest(BaseModel):
    schema_version: Literal["0.1.0"]
    approval_id: UUID
    decision: Literal["approve", "reject"]
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decided_at: datetime
    actor_id: str | None = Field(default=None, max_length=200)
