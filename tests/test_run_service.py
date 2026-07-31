from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.orchestrator.approval.store import SQLitePendingApprovalStore
from app.providers.llm.models import ToolCall
from app.runs.models import RunState
from app.runs.service import RunService
from app.schemas.chat import ChatResponse, ExecutionStep
from app.state.store import SQLiteStateStore


class CompletingOrchestrator:
    def __init__(self, state_store: SQLiteStateStore) -> None:
        self.approval_store = SQLitePendingApprovalStore(state_store=state_store)
        self.calls = 0

    async def handle(self, request, **_kwargs) -> ChatResponse:
        self.calls += 1
        return ChatResponse(
            session_id=request.session_id,
            route="general",
            reply="completed",
            steps=[
                ExecutionStep(
                    name="llm_chat",
                    status="ok",
                    payload={"finish_reason": "stop"},
                ),
                ExecutionStep(name="verification", status="ok", payload={}),
            ],
        )


class ApprovalOrchestrator(CompletingOrchestrator):
    async def handle(self, request, **kwargs) -> ChatResponse:
        approval_id = request.metadata.get("approve_tool_call_id")
        if isinstance(approval_id, str):
            self.approval_store.consume(
                approval_id=approval_id,
                session_id=request.session_id,
            )
            return await super().handle(request, **kwargs)

        pending = self.approval_store.create(
            session_id=request.session_id,
            tool_call=ToolCall(
                id="write-call",
                name="write_file",
                arguments={"path": "result.txt", "content": "safe"},
            ),
            route="coding",
            project_path=request.project_path,
        )
        return ChatResponse(
            session_id=request.session_id,
            route="coding",
            reply="approval required",
            steps=[
                ExecutionStep(
                    name="write_file",
                    status="approval_required",
                    payload={
                        "approval_id": pending.approval_id,
                        "preview_hash": pending.approval_hash,
                    },
                ),
                ExecutionStep(name="verification", status="ok", payload={}),
            ],
        )


class BlockingOrchestrator(CompletingOrchestrator):
    def __init__(self, state_store: SQLiteStateStore) -> None:
        super().__init__(state_store)
        self.started = asyncio.Event()

    async def handle(self, request, **_kwargs) -> ChatResponse:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FailedVerificationOrchestrator(CompletingOrchestrator):
    async def handle(self, request, **_kwargs) -> ChatResponse:
        return ChatResponse(
            session_id=request.session_id,
            route="coding",
            reply="Execution failed code verification.",
            steps=[
                ExecutionStep(
                    name="code_verifier",
                    status="failed",
                    payload={"ok": False},
                )
            ],
        )


class ExplodingOrchestrator(CompletingOrchestrator):
    async def handle(self, request, **_kwargs) -> ChatResponse:
        raise RuntimeError("secret-token-and-traceback")


class TrackingOrchestrator(CompletingOrchestrator):
    def __init__(self, state_store: SQLiteStateStore) -> None:
        super().__init__(state_store)
        self.active = 0
        self.max_active = 0

    async def handle(self, request, **kwargs) -> ChatResponse:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.03)
        self.active -= 1
        return await super().handle(request, **kwargs)


class StreamingOrchestrator(CompletingOrchestrator):
    def __init__(self, state_store: SQLiteStateStore) -> None:
        super().__init__(state_store)
        self.emitted = asyncio.Event()
        self.release = asyncio.Event()

    async def handle(self, request, *, on_step=None) -> ChatResponse:
        llm_step = ExecutionStep(
            name="llm_chat",
            status="ok",
            payload={"finish_reason": "tool_calls"},
        )
        if on_step is not None:
            on_step(llm_step)
        self.emitted.set()
        await self.release.wait()
        verification = ExecutionStep(name="verification", status="ok", payload={})
        if on_step is not None:
            on_step(verification)
        return ChatResponse(
            session_id=request.session_id,
            route="general",
            reply="streamed",
            steps=[llm_step, verification],
        )


def _state(tmp_path) -> tuple[SQLiteStateStore, str]:
    state = SQLiteStateStore(tmp_path / "worker.sqlite3")
    workspace_id = str(uuid4())
    state.register_workspace(
        workspace_id=workspace_id,
        name="Test",
        root_path=tmp_path,
    )
    return state, workspace_id


@pytest.mark.asyncio
async def test_run_completes_with_persistent_timeline(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = CompletingOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.01,
    )

    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="inspect",
        metadata={},
    )
    completed = await service.wait_for_terminal(created.id, timeout_seconds=1)

    assert completed.state is RunState.COMPLETED
    assert completed.result == "completed"
    assert [event.type for event in state.list_run_events(created.id)] == [
        "run_created",
        "run_started",
        "llm_response",
        "verification",
        "run_completed",
    ]

    reopened = SQLiteStateStore(state.path)
    assert reopened.require_run(created.id).state is RunState.COMPLETED
    assert len(reopened.list_run_events(created.id)) == 5


@pytest.mark.asyncio
async def test_run_persists_steps_before_orchestrator_finishes(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = StreamingOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.01,
    )
    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="stream",
        metadata={},
    )

    try:
        await asyncio.wait_for(orchestrator.emitted.wait(), timeout=1)
        assert state.require_run(created.id).state is RunState.RUNNING
        live_types = [event.type for event in state.list_run_events(created.id)]
        assert live_types == ["run_created", "run_started", "llm_response"]
    finally:
        orchestrator.release.set()

    completed = await service.wait_for_terminal(created.id, timeout_seconds=1)
    final_types = [event.type for event in state.list_run_events(created.id)]
    assert completed.state is RunState.COMPLETED
    assert final_types.count("llm_response") == 1


@pytest.mark.asyncio
async def test_run_waits_for_durable_approval_and_resumes(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = ApprovalOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.01,
    )
    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="write a file",
        metadata={},
    )
    waiting = await service.wait_for_terminal(created.id, timeout_seconds=1)
    assert waiting.state is RunState.WAITING_APPROVAL

    approval_event = next(
        event
        for event in state.list_run_events(created.id)
        if event.type == "approval_required"
    )
    approval_id = approval_event.payload["approval_ids"][0]
    approval = state.get_approval_record(approval_id)
    assert approval is not None
    assert approval["run_id"] == created.id

    await service.decide_approval(
        approval_id=approval_id,
        decision="approve",
        preview_hash=approval["approval_hash"],
        decided_at=datetime.now(UTC),
        actor_id="local-user",
    )
    completed = await service.wait_for_terminal(created.id, timeout_seconds=1)

    assert completed.state is RunState.COMPLETED
    assert state.get_approval_record(approval_id)["state"] == "consumed"


@pytest.mark.asyncio
async def test_cancellation_stops_live_task_and_is_persistent(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = BlockingOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.01,
    )
    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="wait",
        metadata={},
    )
    await asyncio.wait_for(orchestrator.started.wait(), timeout=1)

    cancelled = await service.cancel(created.id)
    await asyncio.sleep(0)

    assert cancelled.state is RunState.CANCELLED
    assert state.require_run(created.id).state is RunState.CANCELLED
    assert state.list_run_events(created.id)[-1].type == "run_cancelled"


@pytest.mark.asyncio
async def test_failed_code_verifier_fails_run(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = FailedVerificationOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.01,
    )
    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="fix code",
        metadata={"verify_code": True},
    )

    failed = await service.wait_for_terminal(created.id, timeout_seconds=1)

    assert failed.state is RunState.FAILED
    assert failed.error["code"] == "verification_failed"


@pytest.mark.asyncio
async def test_unexpected_error_is_persisted_without_secret_or_traceback(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = ExplodingOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.01,
    )
    created = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="explode",
        metadata={},
    )

    failed = await service.wait_for_terminal(created.id, timeout_seconds=1)
    persisted = str(failed.error) + str(state.list_run_events(created.id))

    assert failed.state is RunState.FAILED
    assert failed.error["code"] == "run_execution_failed"
    assert "secret-token" not in persisted
    assert "traceback" not in persisted.casefold()


@pytest.mark.asyncio
async def test_workspace_execution_lock_serializes_runs(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    orchestrator = TrackingOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.005,
    )
    first = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="first",
        metadata={},
    )
    second = await service.create_run(
        workspace_id=workspace_id,
        session_id=None,
        message="second",
        metadata={},
    )

    await asyncio.gather(
        service.wait_for_terminal(first.id, timeout_seconds=1),
        service.wait_for_terminal(second.id, timeout_seconds=1),
    )

    assert orchestrator.max_active == 1


def test_recovery_marks_interrupted_run_failed(tmp_path) -> None:
    state, workspace_id = _state(tmp_path)
    run = state.create_run(
        run_id=str(uuid4()),
        workspace_id=workspace_id,
        session_id=str(uuid4()),
        message="interrupted",
        metadata={},
    )
    state.transition_run(
        run_id=run.id,
        new_state=RunState.RUNNING,
        event_type="run_started",
    )
    service = RunService(
        state_store=SQLiteStateStore(state.path),
        orchestrator_factory=lambda _workspace_id: CompletingOrchestrator(state),
    )

    service.start_recovery()

    recovered = state.require_run(run.id)
    assert recovered.state is RunState.FAILED
    assert recovered.error["code"] == "worker_restarted"
