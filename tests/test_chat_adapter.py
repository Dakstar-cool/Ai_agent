from __future__ import annotations

from uuid import uuid4

import pytest

import app.api.routes.chat as chat_routes
import app.api.routes.runs as run_routes
from app.orchestrator.approval.store import SQLitePendingApprovalStore
from app.providers.llm.models import ToolCall
from app.runs.service import RunService
from app.schemas.chat import ChatRequest, ChatResponse, ExecutionStep
from app.state.store import SQLiteStateStore


class ApprovalChatOrchestrator:
    def __init__(self, state_store: SQLiteStateStore) -> None:
        self.approval_store = SQLitePendingApprovalStore(state_store=state_store)
        self.project_paths: list[str | None] = []

    async def handle(self, request: ChatRequest, **_kwargs) -> ChatResponse:
        self.project_paths.append(request.project_path)
        approval_id = request.metadata.get("approve_tool_call_id")
        if isinstance(approval_id, str):
            self.approval_store.consume(
                approval_id=approval_id,
                session_id=request.session_id,
            )
            return ChatResponse(
                session_id=request.session_id,
                route="coding",
                reply="approved and completed",
                steps=[ExecutionStep(name="verification", status="ok", payload={})],
            )

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


@pytest.mark.asyncio
async def test_chat_is_sync_adapter_and_approval_resumes_original_run(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = SQLiteStateStore(tmp_path / "worker.sqlite3")
    workspace = state.register_workspace(
        workspace_id=str(uuid4()),
        name="Default",
        root_path=tmp_path,
    )
    orchestrator = ApprovalChatOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.005,
    )
    monkeypatch.setattr(chat_routes, "get_state_store", lambda: state)
    monkeypatch.setattr(chat_routes, "get_default_workspace", lambda: workspace)
    monkeypatch.setattr(run_routes, "get_run_service", lambda: service)

    initial = await chat_routes.chat(
        ChatRequest(
            message="write a file",
            session_id="legacy-session",
            project_path="C:/untrusted/client/path",
        )
    )
    approval_step = next(
        step for step in initial.steps if step.status == "approval_required"
    )

    resumed = await chat_routes.chat(
        ChatRequest(
            message="approve",
            session_id="legacy-session",
            metadata={"approve_tool_call_id": approval_step.payload["approval_id"]},
        )
    )

    assert initial.session_id == "legacy-session"
    assert resumed.session_id == "legacy-session"
    assert resumed.reply == "approved and completed"
    assert orchestrator.project_paths == [str(tmp_path), str(tmp_path)]

    runs = state.incomplete_runs()
    assert runs == []
