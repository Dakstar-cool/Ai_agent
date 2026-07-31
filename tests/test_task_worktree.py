from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.coding.worktree import TaskWorktreeService
from app.errors import AppError
from app.orchestrator.execution.tool_dispatcher import ToolDispatcher
from app.providers.llm.models import ToolCall
from app.state.store import SQLiteStateStore
from app.tools.git.create_worktree import CreateTaskWorktreeTool
from app.tools.registry import ToolRegistry


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


def _repository(tmp_path) -> tuple[Path, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "AI Agent Test")
    _git(repository, "config", "user.email", "agent-test@example.invalid")
    (repository / "app.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "--", "app.txt")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _service(tmp_path, repository: Path) -> tuple[TaskWorktreeService, str]:
    state = SQLiteStateStore(tmp_path / "state" / "worker.sqlite3")
    workspace_id = str(uuid4())
    state.register_workspace(
        workspace_id=workspace_id,
        name="Source",
        root_path=repository,
    )
    return (
        TaskWorktreeService(
            state_store=state,
            worktree_root=tmp_path / "worktrees",
        ),
        workspace_id,
    )


@pytest.mark.asyncio
async def test_task_worktree_isolates_dirty_source_and_creates_local_commit(
    tmp_path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)
    (repository / "app.txt").write_text("user change\n", encoding="utf-8")

    record = await service.create(
        task_id="task-001",
        source_workspace_id=workspace_id,
        base_sha=base_sha,
    )
    worktree = Path(record.path)

    assert record.branch == "agent/task-001"
    assert record.base_sha == base_sha
    assert (repository / "app.txt").read_text(encoding="utf-8") == "user change\n"
    assert (worktree / "app.txt").read_text(encoding="utf-8") == "base\n"

    (worktree / "app.txt").write_text("agent change\n", encoding="utf-8")
    (worktree / "new.txt").write_text("new file\n", encoding="utf-8")
    report = await service.report("task-001")
    assert report["changed_files"] == ["app.txt", "new.txt"]
    assert "app.txt" in report["diff_stat"]
    assert "new.txt | untracked" in report["diff_stat"]
    assert "-base" in report["unified_diff"]
    assert "+agent change" in report["unified_diff"]
    assert "+++ b/new.txt" in report["unified_diff"]
    assert "+new file" in report["unified_diff"]
    assert report["diff_truncated"] is False

    committed = await service.commit(
        task_id="task-001",
        message="Apply safe agent change",
        paths=["app.txt"],
    )

    assert committed["commit_sha"] != base_sha
    assert _git(repository, "rev-parse", "HEAD") == base_sha
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "agent/task-001"
    assert (repository / "app.txt").read_text(encoding="utf-8") == "user change\n"


@pytest.mark.asyncio
async def test_finalize_verifies_and_optionally_creates_local_commit(
    tmp_path,
    monkeypatch,
) -> None:
    repository, base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)
    record = await service.create(
        task_id="task-finalize",
        source_workspace_id=workspace_id,
        base_sha=base_sha,
    )
    Path(record.path, "app.txt").write_text("verified change\n", encoding="utf-8")

    async def passing_verification(_task_id: str) -> dict:
        return {"ok": True, "checks": [{"name": "pytest", "ok": True}]}

    monkeypatch.setattr(service, "verify", passing_verification)
    result = await service.finalize(
        task_id="task-finalize",
        create_commit=True,
        commit_message="Finalize verified task",
        paths=["app.txt"],
    )

    assert result["diff_report"]["changed_files"] == ["app.txt"]
    assert result["verification_report"]["ok"] is True
    assert result["commit_sha"] != base_sha
    assert _git(repository, "rev-parse", "HEAD") == base_sha


@pytest.mark.asyncio
async def test_task_worktree_rejects_revision_injection(tmp_path) -> None:
    repository, _base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)

    with pytest.raises(AppError) as error:
        await service.create(
            task_id="task-002",
            source_workspace_id=workspace_id,
            base_sha="--help",
        )

    assert error.value.code == "invalid_base_sha"
    assert not (tmp_path / "worktrees" / "task-002").exists()


@pytest.mark.asyncio
async def test_task_worktree_creation_is_idempotent_for_same_task(tmp_path) -> None:
    repository, base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)

    first = await service.create(
        task_id="task-003",
        source_workspace_id=workspace_id,
        base_sha=base_sha,
    )
    second = await service.create(
        task_id="task-003",
        source_workspace_id=workspace_id,
        base_sha=base_sha,
    )

    assert second == first


@pytest.mark.asyncio
async def test_existing_task_worktree_rejects_a_different_handoff_base(tmp_path) -> None:
    repository, base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)
    await service.create(
        task_id="task-handoff",
        source_workspace_id=workspace_id,
        base_sha=base_sha,
    )

    with pytest.raises(AppError) as error:
        await service.create(
            task_id="task-handoff",
            source_workspace_id=workspace_id,
            base_sha="b" * 40,
        )

    assert error.value.code == "handoff_base_mismatch"


@pytest.mark.asyncio
async def test_handoff_base_must_exist_before_worktree_mutation(tmp_path) -> None:
    repository, _base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)

    with pytest.raises(AppError) as error:
        await service.create(
            task_id="task-missing-base",
            source_workspace_id=workspace_id,
            base_sha="b" * 40,
        )

    assert error.value.code == "handoff_base_mismatch"
    assert not (tmp_path / "worktrees" / "task-missing-base").exists()


@pytest.mark.asyncio
async def test_create_worktree_tool_requires_approval_before_git_mutation(tmp_path) -> None:
    repository, base_sha = _repository(tmp_path)
    service, workspace_id = _service(tmp_path, repository)
    registry = ToolRegistry()
    registry.register(
        CreateTaskWorktreeTool(
            service=service,
            source_workspace_id=workspace_id,
        )
    )
    dispatcher = ToolDispatcher(registry)
    call = ToolCall(
        id="worktree-call",
        name="create_task_worktree",
        arguments={"task_id": "task-004", "base_sha": base_sha},
    )

    pending = await dispatcher.execute_call(call)

    assert pending.status == "approval_required"
    assert not (tmp_path / "worktrees" / "task-004").exists()

    approved = await dispatcher.execute_call(call, approved_mutation=True)

    assert approved.status == "ok"
    assert Path(approved.output["path"]).exists()
