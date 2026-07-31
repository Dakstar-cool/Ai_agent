from abc import ABC, abstractmethod
from typing import Any

from app.providers.llm.models import LLMResponse


class ILLMProvider(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> LLMResponse:
        raise NotImplementedError
