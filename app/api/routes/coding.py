from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi import Path as ApiPath

from app.api.routes.chat import require_api_key
from app.coding.worktree import TaskWorktreeService
from app.config.settings import get_settings
from app.errors import AppError
from app.schemas.runs import (
    CommitTaskWorktreeRequest,
    CreateTaskWorktreeRequest,
    FinalizeTaskWorktreeRequest,
    TaskWorktreeResponse,
)
from app.state.runtime import get_state_store

router = APIRouter(dependencies=[Depends(require_api_key)])


@lru_cache(maxsize=1)
def get_worktree_service() -> TaskWorktreeService:
    settings = get_settings()
    return TaskWorktreeService(
        state_store=get_state_store(),
        worktree_root=settings.resolve_task_worktree_root(),
        command_timeout_seconds=settings.tool_command_timeout_seconds,
        max_output_chars=settings.tool_max_output_chars,
    )


@router.post(
    "/task-worktrees",
    response_model=TaskWorktreeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_worktree(
    request: CreateTaskWorktreeRequest,
) -> TaskWorktreeResponse:
    record = await get_worktree_service().create(
        task_id=request.task_id,
        source_workspace_id=str(request.source_workspace_id),
        base_sha=request.base_sha,
    )
    return TaskWorktreeResponse.from_record(record)


@router.get("/task-worktrees/{task_id}", response_model=TaskWorktreeResponse)
async def get_task_worktree(
    task_id: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
) -> TaskWorktreeResponse:
    record = get_state_store().get_task_worktree(task_id)
    if record is None:
        raise AppError(
            message="Task worktree was not found",
            code="task_worktree_not_found",
            status_code=404,
        )
    return TaskWorktreeResponse.from_record(record)


@router.get("/task-worktrees/{task_id}/report")
async def get_task_worktree_report(
    task_id: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
) -> dict[str, Any]:
    return await get_worktree_service().report(task_id)


@router.post("/task-worktrees/{task_id}/verify")
async def verify_task_worktree(
    task_id: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
) -> dict[str, Any]:
    return await get_worktree_service().verify(task_id)


@router.post("/task-worktrees/{task_id}/commit")
async def commit_task_worktree(
    task_id: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
    request: CommitTaskWorktreeRequest,
) -> dict[str, Any]:
    return await get_worktree_service().commit(
        task_id=task_id,
        message=request.message,
        paths=request.paths,
    )


@router.post("/task-worktrees/{task_id}/finalize")
async def finalize_task_worktree(
    task_id: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")],
    request: FinalizeTaskWorktreeRequest,
) -> dict[str, Any]:
    return await get_worktree_service().finalize(
        task_id=task_id,
        create_commit=request.create_commit,
        commit_message=request.commit_message,
        paths=request.paths,
    )
