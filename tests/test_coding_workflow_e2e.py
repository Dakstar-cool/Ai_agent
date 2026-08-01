from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.coding.worktree import TaskWorktreeService
from app.orchestrator.approval.store import SQLitePendingApprovalStore
from app.orchestrator.core import Orchestrator
from app.orchestrator.session.manager import SessionManager
from app.providers.llm.models import LLMResponse, ToolCall
from app.providers.memory.noop import NoOpMemoryService
from app.runs.models import RunState
from app.runs.service import RunService
from app.state.store import SQLiteStateStore
from app.tools.files.write_file import WriteFileTool
from app.tools.registry import ToolRegistry


class CodingProvider:
    def __init__(self) -> None:
        self.responses = [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="write-call",
                        name="write_file",
                        arguments={
                            "path": "app.txt",
                            "content": "agent change\n",
                            "mode": "overwrite",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Waiting for approval"),
            LLMResponse(content="Coding task completed"),
        ]

    async def chat(self, messages, **kwargs):
        return self.responses.pop(0)


class PassingVerifier:
    async def verify(self):
        return {
            "ok": True,
            "checks": [
                {"name": "compileall", "ok": True},
                {"name": "pytest", "ok": True},
                {"name": "ruff", "ok": True},
            ],
        }


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_coding_prompt_changes_only_task_worktree_and_verifies(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.name", "AI Agent Test")
    _git(source, "config", "user.email", "agent-test@example.invalid")
    (source / "app.txt").write_text("base\n", encoding="utf-8")
    _git(source, "add", "--", "app.txt")
    _git(source, "commit", "-m", "base")

    state = SQLiteStateStore(tmp_path / "state" / "worker.sqlite3")
    source_workspace_id = str(uuid4())
    state.register_workspace(
        workspace_id=source_workspace_id,
        name="Source",
        root_path=source,
    )
    worktrees = TaskWorktreeService(
        state_store=state,
        worktree_root=tmp_path / "worktrees",
    )
    task = await worktrees.create(
        task_id="coding-e2e",
        source_workspace_id=source_workspace_id,
        base_sha=_git(source, "rev-parse", "HEAD"),
    )

    registry = ToolRegistry()
    registry.register(WriteFileTool(root_dir=task.path))
    orchestrator = Orchestrator(
        llm_provider=CodingProvider(),
        memory_service=NoOpMemoryService(),
        tool_registry=registry,
        session_manager=SessionManager(state_store=state),
        approval_store=SQLitePendingApprovalStore(state_store=state),
        code_verifier=PassingVerifier(),
    )
    runs = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.005,
    )
    created = await runs.create_run(
        workspace_id=task.worktree_workspace_id,
        session_id=None,
        message="fix code in app.txt",
        metadata={"verify_code": True},
    )
    waiting = await runs.wait_for_terminal(created.id, timeout_seconds=1)
    assert waiting.state is RunState.WAITING_APPROVAL

    approval = next(
        record
        for record in (
            state.get_approval_record(event.payload["approval_ids"][0])
            for event in state.list_run_events(created.id)
            if event.type == "approval_required"
        )
        if record is not None
    )
    await runs.decide_approval(
        approval_id=approval["approval_id"],
        decision="approve",
        preview_hash=approval["approval_hash"],
        decided_at=waiting.updated_at,
        actor_id="local-user",
    )
    completed = await runs.wait_for_terminal(created.id, timeout_seconds=1)
    report = await worktrees.report("coding-e2e")

    assert completed.state is RunState.COMPLETED
    assert completed.result == "Coding task completed"
    assert (source / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (Path(task.path) / "app.txt").read_text(encoding="utf-8") == "agent change\n"
    assert report["changed_files"] == ["app.txt"]
    assert any(
        event.type == "verification" for event in state.list_run_events(created.id)
    )
