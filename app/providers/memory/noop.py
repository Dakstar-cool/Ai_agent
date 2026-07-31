from app.providers.memory.base import IMemoryService
from app.providers.memory.models import (
    MemoryExportItem,
    MemoryRecallItem,
    MemoryRecallQuery,
    MemoryRecord,
    MemoryScopeQuery,
)


class NoOpMemoryService(IMemoryService):
    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        return []

    async def save(self, item: MemoryRecord) -> None:
        return None

    async def export(self, query: MemoryScopeQuery) -> list[MemoryExportItem]:
        return []

    async def delete(self, query: MemoryScopeQuery) -> int:
        return 0
