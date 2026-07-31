from __future__ import annotations

from typing import Any

import httpx

from app.errors import LLMProviderUnavailableError
from app.providers.llm.lmstudio import OpenAICompatibleProvider
from app.providers.llm.models import ProviderCapabilities


class OllamaProvider(OpenAICompatibleProvider):
    provider_name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        api_key: str | None = None,
        max_output_tokens: int = 1_024,
    ) -> None:
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized += "/v1"
        super().__init__(
            normalized,
            model,
            timeout=timeout,
            api_key=api_key,
            max_output_tokens=max_output_tokens,
        )

    async def discover_capabilities(self) -> ProviderCapabilities:
        native_root = self.base_url.removesuffix("/v1")
        try:
            response = await self._client.post(
                f"{native_root}/api/show",
                json={"model": self.model, "verbose": False},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMProviderUnavailableError(
                message="Ollama capability discovery failed",
                details={
                    "provider": self.provider_name,
                    "model": self.model,
                    "reason": exc.__class__.__name__,
                },
            ) from exc

        capabilities = (
            payload.get("capabilities", []) if isinstance(payload, dict) else []
        )
        model_info = payload.get("model_info", {}) if isinstance(payload, dict) else {}
        context_limit = self._ollama_context_limit(model_info)
        return ProviderCapabilities(
            provider=self.provider_name,
            model=self.model,
            tools=isinstance(capabilities, list)
            and any(str(item).casefold() in {"tool", "tools"} for item in capabilities),
            streaming=True,
            context_limit=context_limit,
            available_models=[self.model],
        )

    @staticmethod
    def _ollama_context_limit(model_info: Any) -> int | None:
        if not isinstance(model_info, dict):
            return None
        values = [
            value
            for key, value in model_info.items()
            if str(key).endswith(".context_length")
            and isinstance(value, int)
            and value > 0
        ]
        return max(values) if values else None
