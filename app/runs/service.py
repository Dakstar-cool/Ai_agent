from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.errors import AppError
from app.orchestrator.core import Orchestrator
from app.policy import RunPolicy
from app.runs.models import RunRecord, RunState
from app.schemas.chat import ChatRequest, ChatResponse, ExecutionStep
from app.state.store import SQLiteStateStore


logger = logging.getLogger(__name__)


class RunService:
    def __init__(
        self,
        *,
        state_store: SQLiteStateStore,
        orchestrator_factory: Callable[[str], Orchestrator],
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self.state_store = state_store
        self.orchestrator_factory = orchestrator_factory
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._workspace_locks: dict[str, asyncio.Lock] = {}
        self._recovery_started = False

    async def create_run(
        self,
        *,
        workspace_id: str,
        session_id: str | None,
        message: str,
        metadata: dict[str, Any],
    ) -> RunRecord:
        try:
            policy = RunPolicy.model_validate(
                metadata.get("run_policy", RunPolicy.safe())
            ).activate()
        except ValueError as exc:
            raise AppError(
                message="Run policy is invalid",
                code="invalid_run_policy",
                status_code=400,
            ) from exc
        persisted_metadata = dict(metadata)
        persisted_metadata["run_policy"] = policy.model_dump(mode="json")
        run = self.state_store.create_run(
            run_id=str(uuid4()),
            workspace_id=workspace_id,
            session_id=session_id or str(uuid4()),
            message=message,
            metadata=persisted_metadata,
        )
        self._schedule(run.id)
        return run

    def start_recovery(self) -> None:
        if self._recovery_started:
            return
        self._recovery_started = True
        for run in self.state_store.incomplete_runs():
            if run.state is RunState.QUEUED:
                self._schedule(run.id)
            elif run.state in {RunState.RUNNING, RunState.VERIFYING}:
                self.state_store.transition_run(
                    run_id=run.id,
                    new_state=RunState.FAILED,
                    event_type="run_failed",
                    payload={"reason": "worker_restarted"},
                    error=self._safe_error(
                        code="worker_restarted",
                        message="Run was interrupted by a worker restart",
                    ),
                )

    async def cancel(self, run_id: str) -> RunRecord:
        run = self.state_store.request_cancel(run_id)
        if run.state.terminal:
            return run

        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

        current = self.state_store.require_run(run_id)
        if current.state.terminal:
            return current
        return self.state_store.transition_run(
            run_id=run_id,
            new_state=RunState.CANCELLED,
            event_type="run_cancelled",
            payload={"reason": "cancel_requested"},
            error=self._safe_error(
                code="run_cancelled",
                message="Run was cancelled",
            ),
        )

    async def decide_approval(
        self,
        *,
        approval_id: str,
        decision: str,
        preview_hash: str,
        decided_at: datetime,
        actor_id: str | None,
    ) -> RunRecord:
        record = self.state_store.get_approval_record(approval_id)
        if record is None or record["state"] != "pending":
            raise AppError(
                message="Pending approval was not found",
                code="approval_not_found",
                status_code=404,
            )
        run_id = record["run_id"]
        if not isinstance(run_id, str):
            raise AppError(
                message="Pending approval is not associated with a run",
                code="approval_run_not_found",
                status_code=409,
            )
        run = self.state_store.require_run(run_id)
        if run.state is not RunState.WAITING_APPROVAL:
            raise AppError(
                message="Run is not waiting for approval",
                code="run_not_waiting_approval",
                status_code=409,
            )

        orchestrator = self.orchestrator_factory(run.workspace_id)
        event_payload = {
            "approval_id": approval_id,
            "decision": decision,
            "decided_at": decided_at.isoformat(),
        }
        if actor_id is not None:
            event_payload["actor_id"] = actor_id

        if decision == "reject":
            orchestrator.approval_store.reject(
                approval_id=approval_id,
                expected_hash=preview_hash,
            )
            return self.state_store.transition_run(
                run_id=run.id,
                new_state=RunState.CANCELLED,
                event_type="approval_decided",
                payload=event_payload,
                error=self._safe_error(
                    code="approval_rejected",
                    message="The pending action was rejected",
                ),
            )

        orchestrator.approval_store.validate_hash(
            approval_id=approval_id,
            expected_hash=preview_hash,
        )
        self.state_store.append_run_event(
            run_id=run.id,
            event_type="approval_decided",
            payload=event_payload,
        )
        self.state_store.transition_run(
            run_id=run.id,
            new_state=RunState.RUNNING,
            event_type="run_started",
            payload={"resumed": True, "approval_id": approval_id},
        )
        self._schedule(run.id, approval_id=approval_id)
        return self.state_store.require_run(run.id)

    async def wait_for_terminal(
        self, run_id: str, *, timeout_seconds: float | None = None
    ) -> RunRecord:
        async def wait() -> RunRecord:
            while True:
                run = self.state_store.require_run(run_id)
                if run.state.terminal or run.state is RunState.WAITING_APPROVAL:
                    return run
                await asyncio.sleep(self.poll_interval_seconds)

        if timeout_seconds is None:
            return await wait()
        async with asyncio.timeout(timeout_seconds):
            return await wait()

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _schedule(self, run_id: str, *, approval_id: str | None = None) -> None:
        current = self._tasks.get(run_id)
        if current is not None and not current.done():
            raise AppError(
                message="Run is already executing",
                code="run_already_executing",
                status_code=409,
                details={"run_id": run_id},
            )
        task = asyncio.create_task(
            self._execute(run_id, approval_id=approval_id),
            name=f"ai-agent-run-{run_id}",
        )
        self._tasks[run_id] = task

    async def _execute(self, run_id: str, *, approval_id: str | None) -> None:
        run = self.state_store.require_run(run_id)
        workspace_lock = self._workspace_locks.setdefault(
            run.workspace_id, asyncio.Lock()
        )
        try:
            async with workspace_lock:
                run = self.state_store.require_run(run_id)
                if run.state.terminal:
                    return
                if run.cancel_requested:
                    await self.cancel(run_id)
                    return

                if run.state is not RunState.RUNNING:
                    self.state_store.transition_run(
                        run_id=run_id,
                        new_state=RunState.RUNNING,
                        event_type="run_started",
                        payload={"resumed": approval_id is not None},
                    )
                workspace = self.state_store.get_workspace(run.workspace_id)
                if workspace is None:
                    raise AppError(
                        message="Workspace was not found",
                        code="workspace_not_found",
                        status_code=404,
                    )

                metadata = {
                    key: value
                    for key, value in run.metadata.items()
                    if not key.startswith("_internal_")
                }
                message = run.message
                if approval_id is not None:
                    metadata["approve_tool_call_id"] = approval_id
                    message = "Continue the run after the approved tool call"

                orchestrator = self.orchestrator_factory(run.workspace_id)
                response = await orchestrator.handle(
                    ChatRequest(
                        message=message,
                        session_id=run.session_id,
                        project_path=workspace.root_path,
                        metadata=metadata,
                    )
                )
                await self._finish_response(
                    run_id=run_id,
                    response=response,
                    orchestrator=orchestrator,
                )
        except asyncio.CancelledError:
            current = self.state_store.require_run(run_id)
            if not current.state.terminal:
                self.state_store.transition_run(
                    run_id=run_id,
                    new_state=RunState.CANCELLED,
                    event_type="run_cancelled",
                    payload={"reason": "task_cancelled"},
                    error=self._safe_error(
                        code="run_cancelled",
                        message="Run was cancelled",
                    ),
                )
        except AppError as exc:
            self._fail_run(
                run_id,
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
            )
        except Exception as exc:
            logger.exception(
                "run_execution_failed run_id=%s error=%s",
                run_id,
                exc.__class__.__name__,
            )
            self._fail_run(
                run_id,
                code="run_execution_failed",
                message="Run execution failed",
            )
        finally:
            self._tasks.pop(run_id, None)

    async def _finish_response(
        self,
        *,
        run_id: str,
        response: ChatResponse,
        orchestrator: Orchestrator,
    ) -> None:
        approval_steps: list[ExecutionStep] = []
        verification_failed = False
        has_code_verification = False

        for step in response.steps:
            if step.status == "approval_required":
                approval_steps.append(step)
                continue
            event_type = "tool_result"
            if step.name == "llm_chat":
                event_type = "llm_response"
            elif step.name == "tool_call":
                event_type = "tool_call"
            elif step.name == "policy_audit":
                event_type = "policy_audit"
            elif step.name in {"verification", "code_verifier"}:
                event_type = "verification"
                verification_failed = verification_failed or step.status == "failed"
                has_code_verification = has_code_verification or step.name == "code_verifier"
            self.state_store.append_run_event(
                run_id=run_id,
                event_type=event_type,
                payload={
                    "name": step.name,
                    "status": step.status,
                    "result": step.payload,
                },
            )

        response_payload = response.model_dump(mode="json")
        if approval_steps:
            approval_ids: list[str] = []
            for step in approval_steps:
                approval_id = step.payload.get("approval_id")
                if isinstance(approval_id, str):
                    orchestrator.approval_store.bind_to_run(
                        approval_id=approval_id,
                        run_id=run_id,
                    )
                    approval_ids.append(approval_id)
            self.state_store.transition_run(
                run_id=run_id,
                new_state=RunState.WAITING_APPROVAL,
                event_type="approval_required",
                payload={"approval_ids": approval_ids},
                result=response.reply,
                response_payload=response_payload,
            )
            return

        if has_code_verification:
            self.state_store.transition_run(
                run_id=run_id,
                new_state=RunState.VERIFYING,
                event_type="verification",
                payload={"phase": "completed"},
            )

        if verification_failed:
            self.state_store.transition_run(
                run_id=run_id,
                new_state=RunState.FAILED,
                event_type="run_failed",
                payload={"reason": "verification_failed"},
                result=response.reply,
                response_payload=response_payload,
                error=self._safe_error(
                    code="verification_failed",
                    message="Run failed verification",
                ),
            )
            return

        self.state_store.transition_run(
            run_id=run_id,
            new_state=RunState.COMPLETED,
            event_type="run_completed",
            payload={},
            result=response.reply,
            response_payload=response_payload,
        )

    def _fail_run(
        self,
        run_id: str,
        *,
        code: str,
        message: str,
        status_code: int = 500,
    ) -> None:
        current = self.state_store.require_run(run_id)
        if current.state.terminal:
            return
        self.state_store.transition_run(
            run_id=run_id,
            new_state=RunState.FAILED,
            event_type="run_failed",
            payload={"reason": code},
            error=self._safe_error(
                code=code,
                message=message,
                details={"status_code": status_code},
            ),
        )

    def _safe_error(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {
            "schema_version": "0.3.0",
            "code": code,
            "message": message,
        }
        if details:
            error["details"] = details
        return error
