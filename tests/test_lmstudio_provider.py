from __future__ import annotations

import httpx
import pytest

from app.errors import LLMProviderBadResponseError, LLMProviderUnavailableError
from app.providers.llm.lmstudio import LMStudioProvider
from app.providers.llm.models import LLMResponse


@pytest.mark.asyncio
async def test_lmstudio_provider_maps_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self, url, json):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")
    with pytest.raises(LLMProviderUnavailableError):
        await provider.chat(messages=[{"role": "user", "content": "hello"}])

    await provider.aclose()


@pytest.mark.asyncio
async def test_lmstudio_provider_sends_and_parses_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}

    async def fake_post(self, url, json):
        captured_payload.update(json)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "README.md"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "read",
                "parameters": {"type": "object"},
            },
        }
    ]

    response = await provider.chat(
        messages=[{"role": "user", "content": "read"}],
        tools=tools,
        tool_choice="auto",
    )

    assert isinstance(response, LLMResponse)
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
    assert captured_payload["tools"] == tools
    assert captured_payload["tool_choice"] == "auto"
    await provider.aclose()


@pytest.mark.asyncio
async def test_lmstudio_provider_disables_environment_proxies() -> None:
    provider = LMStudioProvider(
        base_url="http://127.0.0.1:1234/v1",
        model="demo",
        api_key="memory-only-secret",
    )

    assert provider._client._trust_env is False
    assert provider._client.headers["Authorization"] == "Bearer memory-only-secret"
    assert provider.requires_network_permission is False

    await provider.aclose()


@pytest.mark.asyncio
async def test_lmstudio_provider_parses_text_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self, url, json):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "plain answer"},
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")

    response = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response == LLMResponse(content="plain answer", finish_reason="stop")
    await provider.aclose()


@pytest.mark.asyncio
async def test_lmstudio_provider_rejects_invalid_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self, url, json):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "not-json",
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")

    with pytest.raises(LLMProviderBadResponseError):
        await provider.chat(messages=[{"role": "user", "content": "read"}])

    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500])
async def test_lmstudio_provider_does_not_expose_response_secrets(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    async def fake_post(self, url, json):
        return httpx.Response(
            status,
            request=httpx.Request("POST", url),
            text="traceback API_KEY=do-not-expose",
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")

    with pytest.raises(LLMProviderBadResponseError) as error:
        await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert "do-not-expose" not in str(error.value.details)
    assert "traceback" not in str(error.value.details).casefold()
    await provider.aclose()


@pytest.mark.asyncio
async def test_lmstudio_provider_does_not_expose_non_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self, url, json):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            text="traceback SECRET=do-not-expose",
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")

    with pytest.raises(LLMProviderBadResponseError) as error:
        await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert error.value.details["reason"] == "non_json_response"
    assert "do-not-expose" not in str(error.value.details)
    await provider.aclose()
