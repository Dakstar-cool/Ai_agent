from __future__ import annotations

import httpx
import pytest

from app.providers.llm.factory import build_llm_provider
from app.providers.llm.lmstudio import LMStudioProvider, OpenAICompatibleProvider
from app.providers.llm.ollama import OllamaProvider
from app.providers.llm.runtime_config import ProviderRuntimeConfig


@pytest.mark.asyncio
async def test_provider_factory_selects_local_presets_and_generic_remote() -> None:
    lmstudio = build_llm_provider(
        ProviderRuntimeConfig(base_url="http://127.0.0.1:1234/v1", model="local"),
        max_output_tokens=512,
    )
    ollama = build_llm_provider(
        ProviderRuntimeConfig(base_url="http://localhost:11434", model="local")
    )
    remote = build_llm_provider(
        ProviderRuntimeConfig(
            base_url="https://llm.example.test/v1",
            model="remote",
            remote_opt_in=True,
        )
    )

    assert isinstance(lmstudio, LMStudioProvider)
    assert lmstudio.max_output_tokens == 512
    assert isinstance(ollama, OllamaProvider)
    assert ollama.base_url == "http://localhost:11434/v1"
    assert type(remote) is OpenAICompatibleProvider
    await lmstudio.aclose()
    await ollama.aclose()
    await remote.aclose()


def test_provider_factory_rejects_remote_endpoint_without_opt_in() -> None:
    with pytest.raises(ValueError, match="explicit opt-in"):
        build_llm_provider(
            ProviderRuntimeConfig(
                base_url="https://llm.example.test/v1",
                model="remote",
            )
        )


@pytest.mark.asyncio
async def test_openai_compatible_capability_discovery_lists_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self, url):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "data": [
                    {"id": "demo", "context_length": 8_192},
                    {"id": "other"},
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example.test/v1",
        model="demo",
    )

    capabilities = await provider.discover_capabilities()

    assert capabilities.provider == "openai-compatible"
    assert capabilities.available_models == ["demo", "other"]
    assert capabilities.context_limit == 8_192
    assert capabilities.tools is True
    assert capabilities.streaming is True
    await provider.aclose()


@pytest.mark.asyncio
async def test_ollama_capability_discovery_uses_native_show_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "capabilities": ["completion", "tools"],
                "model_info": {"family.context_length": 32_768},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3",
    )

    capabilities = await provider.discover_capabilities()

    assert captured == {
        "url": "http://127.0.0.1:11434/api/show",
        "json": {"model": "qwen3", "verbose": False},
    }
    assert capabilities.provider == "ollama"
    assert capabilities.tools is True
    assert capabilities.context_limit == 32_768
    await provider.aclose()


@pytest.mark.asyncio
async def test_ollama_disables_reasoning_by_default_for_tool_reliability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json):
        captured.update(json)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ready"},
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="qwen3",
    )

    await provider.chat([{"role": "user", "content": "hello"}])

    assert captured["reasoning_effort"] == "none"
    await provider.aclose()
