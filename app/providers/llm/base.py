from abc import ABC, abstractmethod
from typing import Any

from app.providers.llm.models import LLMResponse, ProviderCapabilities


class ILLMProvider(ABC):
    provider_name = "unknown"
    model = "unknown"

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    async def discover_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider_name,
            model=self.model,
            tools=False,
            streaming=False,
            discovered=False,
        )
