from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.errors import AppError
from app.orchestrator.approval.store import (
    PendingApprovalStore,
    SQLitePendingApprovalStore,
)
from app.orchestrator.core import Orchestrator
from app.providers.llm.models import LLMResponse, ToolCall
from app.providers.memory.noop import NoOpMemoryService
from app.schemas.chat import ChatRequest
from app.state.store import SQLiteStateStore
from app.tools.files.write_file import WriteFileTool
from app.tools.registry import ToolRegistry


class SequencedProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(
            {"messages": copy.deepcopy(messages), **copy.deepcopy(kwargs)}
        )
        return self.responses.pop(0)


def _provider(
    *,
    path: str = "approved.txt",
    content: str = "original",
    mode: str | None = None,
):
    arguments = {"path": path, "content": content}
    if mode is not None:
        arguments["mode"] = mode
    return SequencedProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="write-call-1",
                        name="write_file",
                        arguments=arguments,
                    )
                ]
            ),
            LLMResponse(content="Approval required"),
            LLMResponse(content="Approved write completed"),
        ]
    )


def _orchestrator(
    tmp_path,
    provider: SequencedProvider,
    *,
    approval_store: PendingApprovalStore | None = None,
) -> Orchestrator:
    registry = ToolRegistry()
    registry.register(WriteFileTool(root_dir=tmp_path))
    return Orchestrator(
        llm_provider=provider,
        memory_service=NoOpMemoryService(),
        tool_registry=registry,
        approval_store=approval_store,
    )


def _approval_id(response) -> str:
    approval_step = next(
        step for step in response.steps if step.status == "approval_required"
    )
    return approval_step.payload["approval_id"]


def _approval_step(response):
    return next(step for step in response.steps if step.status == "approval_required")


@pytest.mark.asyncio
async def test_approved_write_executes_once_with_stored_arguments(tmp_path) -> None:
    provider = _provider()
    orchestrator = _orchestrator(tmp_path, provider)

    initial = await orchestrator.handle(
        ChatRequest(message="fix code by writing a file", session_id="session-a")
    )
    approval_id = _approval_id(initial)
    preview = _approval_step(initial).payload["mutation_preview"]

    assert not (tmp_path / "approved.txt").exists()
    assert initial.route == "coding"
    assert preview["operation"] == "create"
    assert preview["path"] == "approved.txt"
    assert preview["preview_hash"] == _approval_step(initial).payload["preview_hash"]

    approved = await orchestrator.handle(
        ChatRequest(
            message="approve the pending change",
            session_id="session-a",
            metadata={
                "approve_tool_call_id": approval_id,
                "arguments": {"path": "hijacked.txt", "content": "changed"},
            },
        )
    )

    assert approved.reply == "Approved write completed"
    assert approved.route == "coding"
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "original"
    assert not (tmp_path / "hijacked.txt").exists()

    approved_step = next(
        step
        for step in approved.steps
        if step.name == "write_file" and step.payload.get("approved") is True
    )
    assert approved_step.status == "ok"
    assert approved_step.payload["approval_id"] == approval_id

    third_call_messages = provider.calls[2]["messages"]
    assistant_tool_message = next(
        message for message in third_call_messages if message.get("tool_calls")
    )
    tool_message = next(
        message for message in third_call_messages if message["role"] == "tool"
    )
    assert assistant_tool_message["tool_calls"][0]["id"] == "write-call-1"
    assert tool_message["tool_call_id"] == "write-call-1"
    assert json.loads(tool_message["content"])["result"]["approved"] is True

    with pytest.raises(AppError) as replay_error:
        await orchestrator.handle(
            ChatRequest(
                message="approve again",
                session_id="session-a",
                metadata={"approve_tool_call_id": approval_id},
            )
        )
    assert replay_error.value.code == "approval_not_found"


@pytest.mark.asyncio
async def test_approval_is_bound_to_session(tmp_path) -> None:
    provider = _provider(path="session-bound.txt")
    orchestrator = _orchestrator(tmp_path, provider)
    initial = await orchestrator.handle(
        ChatRequest(message="fix code with a file", session_id="owner-session")
    )
    approval_id = _approval_id(initial)

    with pytest.raises(AppError) as wrong_session_error:
        await orchestrator.handle(
            ChatRequest(
                message="approve",
                session_id="other-session",
                metadata={"approve_tool_call_id": approval_id},
            )
        )
    assert wrong_session_error.value.code == "approval_not_found"
    assert not (tmp_path / "session-bound.txt").exists()

    approved = await orchestrator.handle(
        ChatRequest(
            message="approve",
            session_id="owner-session",
            metadata={"approve_tool_call_id": approval_id},
        )
    )
    assert approved.reply == "Approved write completed"
    assert (tmp_path / "session-bound.txt").exists()


@pytest.mark.asyncio
async def test_expired_approval_is_rejected_without_execution(tmp_path) -> None:
    now = [100.0]
    store = PendingApprovalStore(ttl_seconds=5, clock=lambda: now[0])
    provider = _provider(path="expired.txt")
    orchestrator = _orchestrator(tmp_path, provider, approval_store=store)
    initial = await orchestrator.handle(
        ChatRequest(message="fix code with a file", session_id="session-expired")
    )
    approval_id = _approval_id(initial)
    now[0] = 106.0

    with pytest.raises(AppError) as expired_error:
        await orchestrator.handle(
            ChatRequest(
                message="approve",
                session_id="session-expired",
                metadata={"approve_tool_call_id": approval_id},
            )
        )

    assert expired_error.value.code == "approval_expired"
    assert not (tmp_path / "expired.txt").exists()


@pytest.mark.asyncio
async def test_invalid_approval_id_is_rejected_before_llm_call(tmp_path) -> None:
    provider = _provider()
    orchestrator = _orchestrator(tmp_path, provider)

    with pytest.raises(AppError) as invalid_error:
        await orchestrator.handle(
            ChatRequest(
                message="approve",
                session_id="session-a",
                metadata={"approve_tool_call_id": 123},
            )
        )

    assert invalid_error.value.code == "invalid_approval_request"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_approved_write_rejects_stale_file_state(tmp_path) -> None:
    path = tmp_path / "stale.txt"
    path.write_text("base", encoding="utf-8")
    provider = _provider(path="stale.txt", content="approved", mode="overwrite")
    orchestrator = _orchestrator(tmp_path, provider)
    initial = await orchestrator.handle(
        ChatRequest(message="fix code", session_id="stale-session")
    )
    approval_id = _approval_id(initial)
    path.write_text("changed elsewhere", encoding="utf-8")

    response = await orchestrator.handle(
        ChatRequest(
            message="approve",
            session_id="stale-session",
            metadata={"approve_tool_call_id": approval_id},
        )
    )

    write_step = next(step for step in response.steps if step.name == "write_file")
    assert write_step.status == "failed"
    assert write_step.payload["error"]["code"] == "stale_preview"
    assert path.read_text(encoding="utf-8") == "changed elsewhere"


def test_sqlite_approval_survives_store_restart(tmp_path) -> None:
    state = SQLiteStateStore(tmp_path / "worker.sqlite3")
    state.get_or_create_session("persistent-session")
    first = SQLitePendingApprovalStore(state_store=state)
    pending = first.create(
        session_id="persistent-session",
        tool_call=ToolCall(
            id="persistent-call",
            name="write_file",
            arguments={"path": "result.txt", "content": "safe"},
        ),
        route="coding",
        project_path=None,
    )

    reopened = SQLitePendingApprovalStore(
        state_store=SQLiteStateStore(state.path)
    )
    recovered = reopened.get(pending.approval_id)

    assert recovered.tool_call == pending.tool_call
    assert recovered.approval_hash == pending.approval_hash
    assert reopened.consume(
        approval_id=pending.approval_id,
        session_id="persistent-session",
    ).approval_id == pending.approval_id

    with pytest.raises(AppError) as replay_error:
        reopened.consume(
            approval_id=pending.approval_id,
            session_id="persistent-session",
        )
    assert replay_error.value.code == "approval_not_found"
