from typing import Any

from app.providers.memory.base import IMemoryService


class NoOpMemoryService(IMemoryService):
    async def recall(self, query: str, session_id: str | None = None) -> list[dict[str, Any]]:
        return []

    async def save(self, item: dict[str, Any], session_id: str | None = None) -> None:
        return None
