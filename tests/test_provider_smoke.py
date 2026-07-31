from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from app.providers.llm.base import ILLMProvider
from app.providers.llm.models import LLMResponse, ProviderCapabilities, ToolCall
from app.providers.llm.smoke import SmokeFailure, smoke_tool_provider
from scripts import smoke_ollama as ollama_cli


class FakeToolProvider(ILLMProvider):
    provider_name = "fake-local"
    model = "fake-tool-model"

    def __init__(self, *, tools: bool = True) -> None:
        self.tools = tools
        self.requests: list[dict[str, Any]] = []

    async def discover_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider_name,
            model=self.model,
            tools=self.tools,
            streaming=True,
            available_models=[self.model],
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMResponse:
        self.requests.append({"messages": messages, "kwargs": kwargs})
        definitions = kwargs.get("tools")
        if not definitions:
            return LLMResponse(content="READY")

        name = definitions[0]["function"]["name"]
        prompt = str(messages[-1]["content"])
        if name == "read_file":
            arguments = {"path": "" if "empty path" in prompt else "note.txt"}
        else:
            arguments = {"query": "ai-agent-real-provider-needle"}
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"call-{len(self.requests)}",
                    name=name,
                    arguments=arguments,
                )
            ]
        )


@pytest.mark.asyncio
async def test_shared_provider_smoke_covers_safe_tool_roundtrip() -> None:
    provider = FakeToolProvider()

    result = await smoke_tool_provider(provider, max_tokens=96)

    assert result == {
        "event": "fake_local_smoke_passed",
        "provider": "fake-local",
        "model": "fake-tool-model",
        "checks": [
            "capability_discovery",
            "plain_response",
            "read_file",
            "search_project",
            "malformed_tool_call",
            "tool_call_id_binding",
        ],
    }
    forced_requests = [
        request for request in provider.requests if request["kwargs"].get("tools")
    ]
    assert provider.requests[0]["kwargs"]["max_tokens"] == 96
    assert len(forced_requests) == 3
    assert all(
        request["kwargs"]["tool_choice"] == "required"
        and request["kwargs"]["max_tokens"] == 96
        for request in forced_requests
    )


@pytest.mark.asyncio
async def test_shared_provider_smoke_requires_reported_tool_support() -> None:
    provider = FakeToolProvider(tools=False)

    with pytest.raises(SmokeFailure, match="does not report tool support"):
        await smoke_tool_provider(provider, max_tokens=96)

    assert provider.requests == []


def test_ollama_smoke_cli_redacts_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_without_leaking(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("secret provider response")

    monkeypatch.setattr(ollama_cli, "smoke_ollama", fail_without_leaking)
    monkeypatch.setattr(
        sys,
        "argv",
        ["smoke_ollama.py", "--model", "local-tool-model"],
    )

    assert ollama_cli.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "event": "ollama_smoke_failed",
        "error_type": "RuntimeError",
    }
    assert output.err == ""
    assert "secret provider response" not in output.out
