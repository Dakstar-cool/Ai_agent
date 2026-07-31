from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.RUNNING, RunState.CANCELLED, RunState.FAILED},
    RunState.RUNNING: {
        RunState.WAITING_APPROVAL,
        RunState.VERIFYING,
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.WAITING_APPROVAL: {
        RunState.RUNNING,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.VERIFYING: {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.COMPLETED: set(),
    RunState.FAILED: set(),
    RunState.CANCELLED: set(),
}


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    workspace_id: str
    session_id: str
    state: RunState
    message: str
    metadata: dict[str, Any]
    result: str | None
    response_payload: dict[str, Any] | None
    error: dict[str, Any] | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunEventRecord:
    id: str
    run_id: str
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    id: str
    name: str
    root_path: str
    created_at: datetime
    updated_at: datetime
