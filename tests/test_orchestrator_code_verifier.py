from __future__ import annotations

import pytest

from app.orchestrator.core import Orchestrator
from app.providers.memory.noop import NoOpMemoryService
from app.schemas.chat import ChatRequest
from app.tools.registry import ToolRegistry


class FakeLLMProvider:
    async def chat(self, messages, **kwargs):
        return "ok"


class FakeCodeVerifier:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self.result = result or {"ok": True, "checks": []}
        self.error = error

    async def verify(self) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _orchestrator(code_verifier: FakeCodeVerifier) -> Orchestrator:
    return Orchestrator(
        llm_provider=FakeLLMProvider(),
        memory_service=NoOpMemoryService(),
        tool_registry=ToolRegistry(),
        code_verifier=code_verifier,
    )


@pytest.mark.asyncio
async def test_code_verifier_not_called_without_metadata_flag() -> None:
    verifier = FakeCodeVerifier()
    response = await _orchestrator(verifier).handle(ChatRequest(message="fix code"))

    assert verifier.calls == 0
    assert "code_verifier" not in [step.name for step in response.steps]


@pytest.mark.asyncio
async def test_code_verifier_not_called_for_non_coding_route() -> None:
    verifier = FakeCodeVerifier()
    response = await _orchestrator(verifier).handle(
        ChatRequest(message="hello", metadata={"verify_code": True})
    )

    assert verifier.calls == 0
    assert "code_verifier" not in [step.name for step in response.steps]


@pytest.mark.asyncio
async def test_code_verifier_called_for_coding_route_with_flag() -> None:
    verifier = FakeCodeVerifier({"ok": True, "checks": [{"name": "pytest"}]})
    response = await _orchestrator(verifier).handle(
        ChatRequest(message="fix code", metadata={"verify_code": True})
    )

    code_step = next(step for step in response.steps if step.name == "code_verifier")
    assert verifier.calls == 1
    assert code_step.status == "ok"
    assert code_step.payload["ok"] is True


@pytest.mark.asyncio
async def test_failed_code_verifier_result_is_failed_step() -> None:
    verifier = FakeCodeVerifier({"ok": False, "checks": [{"name": "pytest", "ok": False}]})
    response = await _orchestrator(verifier).handle(
        ChatRequest(message="fix code", metadata={"verify_code": True})
    )

    code_step = next(step for step in response.steps if step.name == "code_verifier")
    assert code_step.status == "failed"
    assert response.reply == "ok"


@pytest.mark.asyncio
async def test_code_verifier_exception_is_safe_failed_step() -> None:
    verifier = FakeCodeVerifier(error=RuntimeError("secret traceback details"))
    response = await _orchestrator(verifier).handle(
        ChatRequest(message="fix code", metadata={"verify_code": True})
    )

    code_step = next(step for step in response.steps if step.name == "code_verifier")
    assert code_step.status == "failed"
    assert code_step.payload == {
        "error": "code_verifier_failed",
        "error_type": "RuntimeError",
    }
    assert "secret traceback details" not in str(code_step.payload)
