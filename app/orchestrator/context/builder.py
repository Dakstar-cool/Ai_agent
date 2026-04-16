from typing import Any

from app.orchestrator.session.manager import SessionState
from app.providers.memory.base import IMemoryService


class ContextBuilder:
    def __init__(self, memory_service: IMemoryService) -> None:
        self.memory_service = memory_service

    async def build(self, session: SessionState, message: str, route: str) -> dict[str, Any]:
        memories = await self.memory_service.recall(query=message, session_id=session.session_id)
        return {
            "system_prompt": (
                "You are a local orchestrator-based AI assistant. "
                f"Current route: {route}. Keep outputs structured and actionable."
            ),
            "history": session.history[-10:],
            "memories": memories,
            "user_message": message,
        }
