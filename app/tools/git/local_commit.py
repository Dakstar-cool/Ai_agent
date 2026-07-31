from __future__ import annotations

from typing import Any, ClassVar

from app.coding.worktree import TaskWorktreeService
from app.errors import AppError
from app.tools.base import ITool


class LocalCommitTool(ITool):
    name = "local_commit"
    description = "Create a local commit for explicit paths in the current task worktree"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            },
            "message": {"type": "string", "minLength": 1, "maxLength": 200},
            "paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 100,
            },
        },
        "required": ["task_id", "message", "paths"],
        "additionalProperties": False,
    }
    mutation_kind = "git_commit"

    def __init__(
        self,
        *,
        service: TaskWorktreeService,
        workspace_id: str,
    ) -> None:
        self.service = service
        self.workspace_id = workspace_id

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        task_id = kwargs.get("task_id")
        if not isinstance(task_id, str):
            raise AppError(
                message="Task ID is required",
                code="invalid_task_id",
                status_code=400,
            )
        record = self.service.state_store.get_task_worktree(task_id)
        if record is None or record.worktree_workspace_id != self.workspace_id:
            raise AppError(
                message="Local commit is limited to the current task worktree",
                code="commit_workspace_mismatch",
                status_code=409,
            )
        paths = kwargs.get("paths")
        if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
            raise AppError(
                message="Commit paths must be a list of strings",
                code="invalid_commit_paths",
                status_code=400,
            )
        message = kwargs.get("message")
        if not isinstance(message, str):
            raise AppError(
                message="Commit message must be a string",
                code="invalid_commit_message",
                status_code=400,
            )
        return await self.service.commit(
            task_id=task_id,
            message=message,
            paths=paths,
        )
