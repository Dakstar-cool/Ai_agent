import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config.settings import get_settings
from app.coding.worktree import TaskWorktreeService
from app.errors import AppError
from app.orchestrator.approval.store import SQLitePendingApprovalStore
from app.orchestrator.core import Orchestrator
from app.orchestrator.session.manager import SessionManager
from app.orchestrator.verification.code_verifier import CodeVerifier
from app.providers.llm.lmstudio import LMStudioProvider
from app.providers.memory.factory import build_memory_service
from app.schemas.chat import ChatRequest, ChatResponse
from app.state.runtime import (
    get_default_workspace,
    get_state_store,
    workspace_id_for_path,
)
from app.state.store import SQLiteStateStore
from app.tools.files.read_file import ReadFileTool
from app.tools.files.write_file import WriteFileTool
from app.tools.git.diff import GitDiffTool
from app.tools.git.create_worktree import CreateTaskWorktreeTool
from app.tools.git.local_commit import LocalCommitTool
from app.tools.git.log import GitLogTool
from app.tools.git.status import GitStatusTool
from app.tools.project.scan_project import ScanProjectTool
from app.tools.project.search_project import SearchProjectTool
from app.tools.registry import ToolRegistry
from app.tools.terminal.run_command import RunCommandTool

router = APIRouter()
_orchestrators: dict[str, Orchestrator] = {}


def get_orchestrator() -> Orchestrator:
    return get_orchestrator_for_workspace(get_default_workspace().id)


def get_orchestrator_for_workspace(workspace_id: str) -> Orchestrator:
    existing = _orchestrators.get(workspace_id)
    if existing is not None:
        return existing

    state_store = get_state_store()
    workspace = state_store.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace_not_found", "message": "Workspace not found"},
        )
    orchestrator = _build_orchestrator(
        tool_root=Path(workspace.root_path),
        state_store=state_store,
    )
    _orchestrators[workspace_id] = orchestrator
    return orchestrator


def _build_orchestrator(
    *, tool_root: Path, state_store: SQLiteStateStore
) -> Orchestrator:
    settings = get_settings()

    llm_provider = LMStudioProvider(
        base_url=settings.lmstudio_base_url,
        model=settings.lmstudio_model,
    )
    memory_service = build_memory_service(settings)
    worktree_service = TaskWorktreeService(
        state_store=state_store,
        worktree_root=settings.resolve_task_worktree_root(),
        command_timeout_seconds=settings.tool_command_timeout_seconds,
        max_output_chars=settings.tool_max_output_chars,
    )
    registry = ToolRegistry()
    registry.register(
        ReadFileTool(root_dir=tool_root, max_bytes=settings.tool_max_file_bytes)
    )
    registry.register(
        WriteFileTool(root_dir=tool_root, max_bytes=settings.tool_max_file_bytes)
    )
    registry.register(
        RunCommandTool(
            root_dir=tool_root,
            allowed_commands=settings.allowed_tool_commands(),
            timeout_seconds=settings.tool_command_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        )
    )
    registry.register(ScanProjectTool(root_dir=tool_root))
    registry.register(
        SearchProjectTool(
            root_dir=tool_root, max_file_bytes=settings.tool_max_file_bytes
        )
    )
    registry.register(
        GitStatusTool(
            root_dir=tool_root,
            timeout_seconds=settings.tool_command_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        )
    )
    registry.register(
        GitDiffTool(
            root_dir=tool_root,
            timeout_seconds=settings.tool_command_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        )
    )
    registry.register(
        GitLogTool(
            root_dir=tool_root,
            timeout_seconds=settings.tool_command_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        )
    )
    workspace_id = workspace_id_for_path(tool_root)
    registry.register(
        CreateTaskWorktreeTool(
            service=worktree_service,
            source_workspace_id=workspace_id,
        )
    )
    registry.register(
        LocalCommitTool(
            service=worktree_service,
            workspace_id=workspace_id,
        )
    )

    return Orchestrator(
        llm_provider=llm_provider,
        memory_service=memory_service,
        tool_registry=registry,
        code_verifier=CodeVerifier(
            root_dir=tool_root,
            timeout_seconds=settings.tool_command_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        ),
        session_manager=SessionManager(
            max_sessions=settings.session_max_sessions,
            max_messages=settings.session_max_messages,
            state_store=state_store,
        ),
        max_steps=settings.agent_max_steps,
        max_tool_calls=settings.agent_max_tool_calls,
        agent_timeout_seconds=settings.agent_timeout_seconds,
        approval_store=SQLitePendingApprovalStore(
            state_store=state_store,
            ttl_seconds=settings.approval_ttl_seconds,
            max_pending=settings.approval_max_pending,
        ),
    )


async def close_orchestrator() -> None:
    orchestrators = list(_orchestrators.values())
    _orchestrators.clear()
    closes = []
    for orchestrator in orchestrators:
        close = getattr(orchestrator.llm_provider, "aclose", None)
        if close is not None:
            closes.append(close())
    if closes:
        await asyncio.gather(*closes)


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_settings()
    if not settings.api_key:
        return

    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()

    if x_api_key == settings.api_key or bearer == settings.api_key:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": "Invalid API key"},
    )


@router.post(
    "/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)]
)
async def chat(request: ChatRequest) -> ChatResponse:
    from app.api.routes.runs import get_run_service

    service = get_run_service()
    state_store = get_state_store()
    raw_approval_id = request.metadata.get("approve_tool_call_id")
    if raw_approval_id is not None:
        if not isinstance(raw_approval_id, str) or not raw_approval_id.strip():
            raise AppError(
                message="approve_tool_call_id must be a non-empty string",
                code="invalid_approval_request",
                status_code=400,
            )
        approval_id = raw_approval_id.strip()
        approval = state_store.get_approval_record(approval_id)
        if approval is None or approval["state"] != "pending":
            raise AppError(
                message="Pending approval was not found for this session",
                code="approval_not_found",
                status_code=404,
            )
        if request.session_id is None:
            raise AppError(
                message="Pending approval was not found for this session",
                code="approval_not_found",
                status_code=404,
            )
        compat_session_id = _compat_session_id(request.session_id)
        if approval["session_id"] != compat_session_id:
            raise AppError(
                message="Pending approval was not found for this session",
                code="approval_not_found",
                status_code=404,
            )
        run = await service.decide_approval(
            approval_id=approval_id,
            decision="approve",
            preview_hash=str(approval["approval_hash"]),
            decided_at=datetime.now(UTC),
            actor_id="compat-chat",
        )
    else:
        metadata = dict(request.metadata)
        session_id = (
            _compat_session_id(request.session_id)
            if request.session_id is not None
            else None
        )
        if request.session_id is not None:
            metadata["_internal_compat_session_id"] = request.session_id
        run = await service.create_run(
            workspace_id=get_default_workspace().id,
            session_id=session_id,
            message=request.message,
            metadata=metadata,
        )

    settings = get_settings()
    timeout_seconds = (
        settings.agent_timeout_seconds
        + (3 * settings.tool_command_timeout_seconds)
        + 15
    )
    try:
        run = await service.wait_for_terminal(
            run.id,
            timeout_seconds=timeout_seconds,
        )
    except TimeoutError as exc:
        raise AppError(
            message="Compatibility chat request timed out",
            code="chat_adapter_timeout",
            status_code=504,
        ) from exc

    if run.response_payload is not None:
        response = ChatResponse.model_validate(run.response_payload)
        if request.session_id is not None:
            response = response.model_copy(update={"session_id": request.session_id})
        return response

    error = run.error or {}
    details = error.get("details")
    status_code = details.get("status_code", 500) if isinstance(details, dict) else 500
    raise AppError(
        message=str(error.get("message", "Run execution failed")),
        code=str(error.get("code", "run_execution_failed")),
        status_code=int(status_code),
    )


def _compat_session_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"ai-agent-compat-session:{value}"))
