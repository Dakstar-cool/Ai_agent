from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config.settings import get_settings
from app.orchestrator.core import Orchestrator
from app.orchestrator.session.manager import SessionManager
from app.providers.llm.lmstudio import LMStudioProvider
from app.providers.memory.factory import build_memory_service
from app.schemas.chat import ChatRequest, ChatResponse
from app.tools.files.read_file import ReadFileTool
from app.tools.files.write_file import WriteFileTool
from app.tools.git.diff import GitDiffTool
from app.tools.git.log import GitLogTool
from app.tools.git.status import GitStatusTool
from app.tools.project.scan_project import ScanProjectTool
from app.tools.project.search_project import SearchProjectTool
from app.tools.registry import ToolRegistry
from app.tools.terminal.run_command import RunCommandTool

router = APIRouter()


@lru_cache(maxsize=1)
def get_orchestrator() -> Orchestrator:
    settings = get_settings()

    llm_provider = LMStudioProvider(
        base_url=settings.lmstudio_base_url,
        model=settings.lmstudio_model,
    )
    memory_service = build_memory_service(settings)
    tool_root = settings.resolve_project_path(settings.tool_workspace_root)

    registry = ToolRegistry()
    registry.register(ReadFileTool(root_dir=tool_root, max_bytes=settings.tool_max_file_bytes))
    registry.register(WriteFileTool(root_dir=tool_root, max_bytes=settings.tool_max_file_bytes))
    registry.register(
        RunCommandTool(
            root_dir=tool_root,
            allowed_commands=settings.allowed_tool_commands(),
            timeout_seconds=settings.tool_command_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        )
    )
    registry.register(ScanProjectTool(root_dir=tool_root))
    registry.register(SearchProjectTool(root_dir=tool_root, max_file_bytes=settings.tool_max_file_bytes))
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

    return Orchestrator(
        llm_provider=llm_provider,
        memory_service=memory_service,
        tool_registry=registry,
        session_manager=SessionManager(
            max_sessions=settings.session_max_sessions,
            max_messages=settings.session_max_messages,
        ),
    )


async def close_orchestrator() -> None:
    if get_orchestrator.cache_info().currsize == 0:
        return

    orchestrator = get_orchestrator()
    close = getattr(orchestrator.llm_provider, "aclose", None)
    if close is not None:
        await close()
    get_orchestrator.cache_clear()


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


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def chat(request: ChatRequest) -> ChatResponse:
    orchestrator = get_orchestrator()
    return await orchestrator.handle(request)
