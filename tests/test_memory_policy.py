from __future__ import annotations

import pytest

from app.orchestrator.core import Orchestrator
from app.providers.memory.models import MemoryRecallItem, MemoryRecallQuery, MemoryRecord
from app.providers.memory.policy import contains_sensitive_data
from app.schemas.chat import ChatRequest
from app.tools.registry import ToolRegistry


class FakeLLMProvider:
    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply

    async def chat(self, messages, **kwargs) -> str:
        return self.reply


class RecordingMemoryService:
    def __init__(self) -> None:
        self.saved: list[MemoryRecord] = []

    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        return []

    async def save(self, item: MemoryRecord) -> None:
        self.saved.append(item)


async def _run(message: str, *, metadata: dict | None = None, reply: str = "ok") -> RecordingMemoryService:
    memory = RecordingMemoryService()
    orchestrator = Orchestrator(
        llm_provider=FakeLLMProvider(reply),
        memory_service=memory,
        tool_registry=ToolRegistry(),
    )
    await orchestrator.handle(
        ChatRequest(message=message, metadata=metadata or {}, session_id="test")
    )
    return memory


def test_contains_sensitive_data_detects_secret_patterns() -> None:
    assert contains_sensitive_data("API_KEY=abc") is True
    assert contains_sensitive_data({"nested": {"access_token": "abc"}}) is True
    assert contains_sensitive_data("plain project note") is False


@pytest.mark.asyncio
async def test_normal_message_is_saved() -> None:
    memory = await _run("remember this normal note")

    assert len(memory.saved) == 1


@pytest.mark.asyncio
async def test_api_key_in_message_is_not_saved() -> None:
    memory = await _run("API_KEY=abc123")

    assert memory.saved == []


@pytest.mark.asyncio
async def test_bearer_token_in_message_is_not_saved() -> None:
    memory = await _run("Authorization: Bearer abc123")

    assert memory.saved == []


@pytest.mark.asyncio
async def test_token_in_metadata_is_not_saved() -> None:
    memory = await _run("normal message", metadata={"token": "abc123"})

    assert memory.saved == []


@pytest.mark.asyncio
async def test_private_key_marker_in_llm_reply_is_not_saved() -> None:
    memory = await _run("normal message", reply="-----BEGIN PRIVATE KEY-----")

    assert memory.saved == []
