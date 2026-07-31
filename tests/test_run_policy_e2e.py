from __future__ import annotations

from uuid import uuid4

import pytest

from app.orchestrator.core import Orchestrator
from app.orchestrator.session.manager import SessionManager
from app.policy import AutonomyMode, RunPolicy
from app.providers.llm.models import LLMResponse, ToolCall
from app.providers.memory.noop import NoOpMemoryService
from app.runs.models import RunState
from app.runs.service import RunService
from app.state.store import SQLiteStateStore
from app.tools.files.write_file import WriteFileTool
from app.tools.registry import ToolRegistry


class AutonomousWriteProvider:
    def __init__(self) -> None:
        self.responses = [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="autonomous-write",
                        name="write_file",
                        arguments={
                            "path": "src/app.py",
                            "content": "autonomous change\n",
                            "mode": "overwrite",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Autonomous task completed", finish_reason="stop"),
        ]

    async def chat(self, messages, **kwargs):
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_autonomous_run_persists_mutation_audit_without_approval(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.joinpath("src").mkdir(parents=True)
    workspace.joinpath("src", "app.py").write_text("base\n", encoding="utf-8")
    state = SQLiteStateStore(tmp_path / "state" / "worker.sqlite3")
    workspace_id = str(uuid4())
    state.register_workspace(
        workspace_id=workspace_id,
        name="Policy E2E",
        root_path=workspace,
    )
    registry = ToolRegistry()
    registry.register(WriteFileTool(root_dir=workspace))
    orchestrator = Orchestrator(
        llm_provider=AutonomousWriteProvider(),
        memory_service=NoOpMemoryService(),
        tool_registry=registry,
        session_manager=SessionManager(state_store=state),
    )
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.005,
    )
    policy = RunPolicy(
        mode=AutonomyMode.AUTONOMOUS,
        ttl_seconds=300,
        allowed_tools={"write_file"},
        path_globs=("src/**",),
        max_writes=1,
    ).activate()

    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="fix src/app.py",
        metadata={"run_policy": policy.model_dump(mode="json")},
    )
    completed = await service.wait_for_terminal(created.id, timeout_seconds=1)
    events = state.list_run_events(created.id)

    assert completed.state is RunState.COMPLETED
    assert workspace.joinpath("src", "app.py").read_text(encoding="utf-8") == (
        "autonomous change\n"
    )
    assert not any(event.type == "approval_required" for event in events)
    audit = next(event for event in events if event.type == "policy_audit")
    assert audit.payload["result"]["decision"]["mode"] == "autonomous"
    assert len(audit.payload["result"]["post_action_hash"]) == 64
