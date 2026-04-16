from abc import ABC, abstractmethod
from typing import Any


class IMemoryService(ABC):
    @abstractmethod
    async def recall(self, query: str, session_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, item: dict[str, Any], session_id: str | None = None) -> None:
        raise NotImplementedError
