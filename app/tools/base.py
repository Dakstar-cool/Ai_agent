from abc import ABC, abstractmethod
from typing import Any


class ITool(ABC):
    name: str
    description: str

    @abstractmethod
    async def run(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError
