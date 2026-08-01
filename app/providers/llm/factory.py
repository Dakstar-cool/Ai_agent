from __future__ import annotations

from urllib.parse import urlsplit

from app.providers.llm.lmstudio import LMStudioProvider, OpenAICompatibleProvider
from app.providers.llm.ollama import OllamaProvider
from app.providers.llm.runtime_config import ProviderRuntimeConfig


def build_llm_provider(
    config: ProviderRuntimeConfig,
    *,
    timeout: float = 60.0,
    max_output_tokens: int = 1_024,
) -> OpenAICompatibleProvider:
    config = config.validate()
    parsed = urlsplit(config.base_url)
    hostname = (parsed.hostname or "").casefold()
    if hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 11434:
        provider_type = OllamaProvider
    elif hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 1234:
        provider_type = LMStudioProvider
    else:
        provider_type = OpenAICompatibleProvider
    return provider_type(
        base_url=config.base_url,
        model=config.model,
        timeout=timeout,
        api_key=config.api_key,
        max_output_tokens=max_output_tokens,
    )
