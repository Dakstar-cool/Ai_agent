from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.policy import RunPolicy
from app.runs.models import (
    RunEventRecord,
    RunRecord,
    RunState,
    TaskWorktreeRecord,
    WorkspaceRecord,
)
from app.schemas.chat import MAX_MESSAGE_LENGTH, MAX_METADATA_BYTES


class CreateRunRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    workspace_id: UUID
    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    metadata: dict[str, Any] = Field(default_factory=dict)
    policy: RunPolicy = Field(default_factory=RunPolicy.safe)

    @model_validator(mode="after")
    def validate_metadata_size(self) -> CreateRunRequest:
        size = len(json.dumps(self.metadata, ensure_ascii=False).encode("utf-8"))
        if size > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be at most {MAX_METADATA_BYTES} bytes")
        return self


class RunResponse(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    id: UUID
    workspace_id: UUID
    session_id: UUID
    state: RunState
    created_at: datetime
    updated_at: datetime
    result: str | None = None
    error: dict[str, Any] | None = None

    @classmethod
    def from_record(cls, record: RunRecord) -> RunResponse:
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
    schema_version: Literal["0.3.0"] = "0.3.0"
    id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    type: str
    created_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_record(cls, record: RunEventRecord) -> RunEventResponse:
        return cls(
            id=record.id,
            run_id=record.run_id,
            sequence=record.sequence,
            type=record.type,
            created_at=record.created_at,
            payload=record.payload,
        )


class RegisterWorkspaceRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1, max_length=1_000)


class WorkspaceResponse(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    id: UUID
    name: str
    root_path: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: WorkspaceRecord) -> WorkspaceResponse:
        return cls(
            id=record.id,
            name=record.name,
            root_path=record.root_path,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ApprovalDecisionRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    approval_id: UUID
    decision: Literal["approve", "reject"]
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decided_at: datetime
    actor_id: str | None = Field(default=None, max_length=200)


class PendingApprovalResponse(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    id: UUID
    run_id: UUID
    tool_call_id: str
    tool_name: str
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mutation_preview: dict[str, Any] | None = None
    expires_at: datetime


class CreateTaskWorktreeRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    source_workspace_id: UUID
    base_sha: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{7,64}$")


class TaskWorktreeResponse(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    task_id: str
    source_workspace_id: UUID
    worktree_workspace_id: UUID
    branch: str
    base_sha: str
    path: str
    created_at: datetime

    @classmethod
    def from_record(cls, record: TaskWorktreeRecord) -> TaskWorktreeResponse:
        return cls(
            task_id=record.task_id,
            source_workspace_id=record.source_workspace_id,
            worktree_workspace_id=record.worktree_workspace_id,
            branch=record.branch,
            base_sha=record.base_sha,
            path=record.path,
            created_at=record.created_at,
        )


class CommitTaskWorktreeRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    message: str = Field(min_length=1, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=100)


class FinalizeTaskWorktreeRequest(BaseModel):
    schema_version: Literal["0.3.0"]
    create_commit: bool = False
    commit_message: str | None = Field(default=None, min_length=1, max_length=200)
    paths: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_commit_fields(self) -> FinalizeTaskWorktreeRequest:
        if self.create_commit and (self.commit_message is None or not self.paths):
            raise ValueError(
                "commit_message and paths are required when create_commit=true"
            )
        return self
