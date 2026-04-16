from __future__ import annotations

from abc import ABC, abstractmethod

from app.providers.memory.models import MemoryRecallItem, MemoryRecallQuery, MemoryRecord


class IMemoryService(ABC):
    @abstractmethod
    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, item: MemoryRecord) -> None:
        raise NotImplementedError
