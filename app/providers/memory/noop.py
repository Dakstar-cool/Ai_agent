from app.providers.memory.base import IMemoryService
from app.providers.memory.models import MemoryRecallItem, MemoryRecallQuery, MemoryRecord


class NoOpMemoryService(IMemoryService):
    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        return []

    async def save(self, item: MemoryRecord) -> None:
        return None
