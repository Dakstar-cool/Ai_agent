from abc import ABC, abstractmethod
from typing import Any


class ITool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    mutation_kind: str | None = None
    network_access: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    @abstractmethod
    async def run(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError
