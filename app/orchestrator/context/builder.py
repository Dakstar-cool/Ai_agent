from typing import Any

from app.orchestrator.session.manager import SessionState
from app.providers.memory.base import IMemoryService
from app.providers.memory.models import MemoryRecallQuery


class ContextBuilder:
    def __init__(self, memory_service: IMemoryService) -> None:
        self.memory_service = memory_service

    async def build(
        self,
        session: SessionState,
        message: str,
        route: str,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        memories = await self.memory_service.recall(
            MemoryRecallQuery(
                text=message,
                session_id=session.session_id,
                project_path=project_path,
                route=route,
            )
        )
        return {
            "system_prompt": (
                "You are a local orchestrator-based AI assistant. "
                f"Current route: {route}. Keep outputs structured and actionable. "
                "Use recalled memories when they are relevant. "
                "If the user asks what you remember or what project you are working on, "
                "summarize the relevant memories first and only then answer the request."
            ),
            "history": session.history[-10:],
            "memories": memories,
            "user_message": message,
            "project_path": project_path,
        }
