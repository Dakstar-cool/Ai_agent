from __future__ import annotations

from abc import ABC, abstractmethod

from app.providers.memory.models import (
    MemoryExportItem,
    MemoryRecallItem,
    MemoryRecallQuery,
    MemoryRecord,
    MemoryScopeQuery,
)


class IMemoryService(ABC):
    @abstractmethod
    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, item: MemoryRecord) -> None:
        raise NotImplementedError

    async def export(self, query: MemoryScopeQuery) -> list[MemoryExportItem]:
        raise NotImplementedError

    async def delete(self, query: MemoryScopeQuery) -> int:
        raise NotImplementedError
