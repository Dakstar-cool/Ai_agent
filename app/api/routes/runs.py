from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Path as ApiPath, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.routes.chat import require_api_key
from app.config.settings import get_settings
from app.errors import AppError
from app.runs.service import RunService
from app.schemas.runs import (
    ApprovalDecisionRequest,
    CreateRunRequest,
    PendingApprovalResponse,
    RegisterWorkspaceRequest,
    RunEventResponse,
    RunResponse,
    WorkspaceResponse,
)
from app.state.runtime import (
    get_default_workspace,
    get_state_store,
    workspace_id_for_path,
)


router = APIRouter(dependencies=[Depends(require_api_key)])


def _get_orchestrator_for_run(workspace_id: str):
    import app.api.routes.chat as chat_routes

    if workspace_id == get_default_workspace().id:
        return chat_routes.get_orchestrator()
    return chat_routes.get_orchestrator_for_workspace(workspace_id)


@lru_cache(maxsize=1)
def get_run_service() -> RunService:
    settings = get_settings()
    service = RunService(
        state_store=get_state_store(),
        orchestrator_factory=_get_orchestrator_for_run,
        poll_interval_seconds=settings.run_event_poll_interval_seconds,
    )
    service.start_recovery()
    return service


async def close_run_service() -> None:
    if get_run_service.cache_info().currsize == 0:
        return
    service = get_run_service()
    await service.close()
    get_run_service.cache_clear()


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=201)
async def register_workspace(
    request: RegisterWorkspaceRequest,
) -> WorkspaceResponse:
    try:
        root = Path(request.root_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise AppError(
            message="Workspace root must be an existing directory",
            code="invalid_workspace_root",
            status_code=400,
        ) from exc
    workspace = get_state_store().register_workspace(
        workspace_id=workspace_id_for_path(root),
        name=request.name,
        root_path=root,
    )
    return WorkspaceResponse.from_record(workspace)


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces() -> list[WorkspaceResponse]:
    return [
        WorkspaceResponse.from_record(workspace)
        for workspace in get_state_store().list_workspaces()
    ]


@router.post("/runs", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(request: CreateRunRequest) -> RunResponse:
    metadata = dict(request.metadata)
    metadata["run_policy"] = request.policy.model_dump(mode="json")
    run = await get_run_service().create_run(
        workspace_id=str(request.workspace_id),
        session_id=str(request.session_id) if request.session_id is not None else None,
        message=request.message,
        metadata=metadata,
    )
    return RunResponse.from_record(run)


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(
    workspace_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunResponse]:
    state_store = get_state_store()
    if workspace_id is not None and state_store.get_workspace(workspace_id) is None:
        raise AppError(
            message="Workspace was not found",
            code="workspace_not_found",
            status_code=404,
        )
    return [
        RunResponse.from_record(run)
        for run in state_store.list_runs(workspace_id=workspace_id, limit=limit)
    ]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: Annotated[str, ApiPath(pattern=r"^[a-f0-9-]{32,36}$")],
) -> RunResponse:
    return RunResponse.from_record(get_state_store().require_run(run_id))


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    run_id: Annotated[str, ApiPath(pattern=r"^[a-f0-9-]{32,36}$")],
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    state_store = get_state_store()
    state_store.require_run(run_id)
    poll_interval = get_settings().run_event_poll_interval_seconds

    async def event_stream():
        last_sequence = after
        while True:
            events = state_store.list_run_events(
                run_id,
                after_sequence=last_sequence,
            )
            for event in events:
                response = RunEventResponse.from_record(event)
                last_sequence = event.sequence
                yield (
                    f"id: {event.sequence}\n"
                    f"event: {event.type}\n"
                    f"data: {response.model_dump_json()}\n\n"
                )

            run = state_store.require_run(run_id)
            if run.state.terminal and not events:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: Annotated[str, ApiPath(pattern=r"^[a-f0-9-]{32,36}$")],
) -> RunResponse:
    return RunResponse.from_record(await get_run_service().cancel(run_id))


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def decide_approval(
    approval_id: Annotated[str, ApiPath(pattern=r"^[a-f0-9-]{32,36}$")],
    decision: ApprovalDecisionRequest,
) -> RunResponse:
    if approval_id.replace("-", "").casefold() != decision.approval_id.hex.casefold():
        raise AppError(
            message="Approval ID in path and body must match",
            code="approval_id_mismatch",
            status_code=400,
        )
    run = await get_run_service().decide_approval(
        approval_id=approval_id,
        decision=decision.decision,
        preview_hash=decision.preview_hash,
        decided_at=decision.decided_at,
        actor_id=decision.actor_id,
    )
    return RunResponse.from_record(run)


@router.get(
    "/approvals/{approval_id}",
    response_model=PendingApprovalResponse,
)
async def get_pending_approval(
    approval_id: Annotated[str, ApiPath(pattern=r"^[a-f0-9-]{32,36}$")],
) -> PendingApprovalResponse:
    record = get_state_store().get_approval_record(approval_id)
    if record is None or record["state"] != "pending":
        raise AppError(
            message="Pending approval was not found",
            code="approval_not_found",
            status_code=404,
        )
    run_id = record["run_id"]
    tool_call = record["tool_call"]
    if not isinstance(run_id, str) or not isinstance(tool_call, dict):
        raise AppError(
            message="Pending approval is invalid",
            code="approval_invalid",
            status_code=409,
        )
    return PendingApprovalResponse(
        id=record["approval_id"],
        run_id=run_id,
        tool_call_id=str(tool_call.get("id", "unknown")),
        tool_name=str(tool_call.get("name", "unknown")),
        preview_hash=str(record["approval_hash"]),
        mutation_preview=(
            record["mutation_preview"]
            if isinstance(record["mutation_preview"], dict)
            else None
        ),
        expires_at=record["expires_at"],
    )
