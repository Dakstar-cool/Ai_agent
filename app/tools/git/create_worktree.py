from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.coding.worktree import TaskWorktreeService
from app.errors import AppError
from app.tools.base import ITool


class CreateTaskWorktreeTool(ITool):
    name = "create_task_worktree"
    description = "Create an isolated agent/<task-id> branch and git worktree"
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            },
            "base_sha": {
                "type": "string",
                "pattern": "^[a-fA-F0-9]{7,64}$",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        service: TaskWorktreeService,
        source_workspace_id: str,
    ) -> None:
        self.service = service
        self.source_workspace_id = source_workspace_id

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        task_id = kwargs.get("task_id")
        base_sha = kwargs.get("base_sha")
        if not isinstance(task_id, str):
            raise AppError(
                message="Task ID is required",
                code="invalid_task_id",
                status_code=400,
            )
        if base_sha is not None and not isinstance(base_sha, str):
            raise AppError(
                message="Base commit must be a string",
                code="invalid_base_sha",
                status_code=400,
            )
        record = await self.service.create(
            task_id=task_id,
            source_workspace_id=self.source_workspace_id,
            base_sha=base_sha,
        )
        return asdict(record)
