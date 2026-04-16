from functools import lru_cache

from fastapi import APIRouter

from app.config.settings import get_settings
from app.orchestrator.core import Orchestrator
from app.providers.llm.lmstudio import LMStudioProvider
from app.providers.memory.noop import NoOpMemoryService
from app.schemas.chat import ChatRequest, ChatResponse
from app.tools.files.read_file import ReadFileTool
from app.tools.files.write_file import WriteFileTool
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
    memory_service = NoOpMemoryService()

    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(RunCommandTool())

    return Orchestrator(
        llm_provider=llm_provider,
        memory_service=memory_service,
        tool_registry=registry,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    orchestrator = get_orchestrator()
    return await orchestrator.handle(request)
