from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.routes.runs as run_routes
from app.main import create_app
from app.orchestrator.approval.store import SQLitePendingApprovalStore
from app.providers.llm.models import ToolCall
from app.runs.models import RunState
from app.runs.service import RunService
from app.schemas.chat import ChatResponse, ExecutionStep
from app.state.store import SQLiteStateStore


class ApiOrchestrator:
    def __init__(self, state_store: SQLiteStateStore) -> None:
        self.approval_store = SQLitePendingApprovalStore(state_store=state_store)

    async def handle(self, request) -> ChatResponse:
        return ChatResponse(
            session_id=request.session_id,
            route="general",
            reply="api completed",
            steps=[
                ExecutionStep(
                    name="llm_chat",
                    status="ok",
                    payload={"finish_reason": "stop"},
                ),
                ExecutionStep(name="verification", status="ok", payload={}),
            ],
        )


@pytest.fixture
def run_api(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = SQLiteStateStore(tmp_path / "worker.sqlite3")
    workspace_id = str(uuid4())
    state.register_workspace(
        workspace_id=workspace_id,
        name="API test",
        root_path=tmp_path,
    )
    orchestrator = ApiOrchestrator(state)
    service = RunService(
        state_store=state,
        orchestrator_factory=lambda _workspace_id: orchestrator,
        poll_interval_seconds=0.005,
    )
    monkeypatch.setattr(run_routes, "get_state_store", lambda: state)

    def fake_get_run_service() -> RunService:
        return service

    fake_get_run_service.cache_info = lambda: SimpleNamespace(currsize=1)
    fake_get_run_service.cache_clear = lambda: None
    monkeypatch.setattr(run_routes, "get_run_service", fake_get_run_service)

    with TestClient(create_app()) as client:
        yield client, state, workspace_id, orchestrator


def test_run_api_creates_polls_and_streams_timeline(run_api) -> None:
    client, _state, workspace_id, _orchestrator = run_api
    created_response = client.post(
        "/api/v1/runs",
        json={
            "schema_version": "0.3.0",
            "workspace_id": workspace_id,
            "message": "inspect",
        },
    )

    assert created_response.status_code == 202
    run_id = created_response.json()["id"]

    deadline = time.monotonic() + 2
    current = created_response.json()
    while current["state"] not in {"completed", "failed", "cancelled"}:
        assert time.monotonic() < deadline
        current = client.get(f"/api/v1/runs/{run_id}").json()
        time.sleep(0.01)

    assert current["state"] == "completed"
    assert current["result"] == "api completed"

    events = client.get(f"/api/v1/runs/{run_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: run_created" in events.text
    assert "event: run_completed" in events.text
    assert '"schema_version":"0.3.0"' in events.text


def test_run_api_rejects_unknown_workspace(run_api) -> None:
    client, _state, _workspace_id, _orchestrator = run_api

    response = client.post(
        "/api/v1/runs",
        json={
            "schema_version": "0.3.0",
            "workspace_id": str(uuid4()),
            "message": "inspect",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workspace_not_found"


def test_run_api_lists_history_for_selected_workspace(run_api) -> None:
    client, state, workspace_id, _orchestrator = run_api
    other_workspace_id = str(uuid4())
    other_root = state.path.parent / "other-workspace"
    other_root.mkdir()
    state.register_workspace(
        workspace_id=other_workspace_id,
        name="Other",
        root_path=other_root,
    )
    selected = state.create_run(
        run_id=str(uuid4()),
        workspace_id=workspace_id,
        session_id=str(uuid4()),
        message="selected",
        metadata={},
    )
    state.create_run(
        run_id=str(uuid4()),
        workspace_id=other_workspace_id,
        session_id=str(uuid4()),
        message="other",
        metadata={},
    )

    response = client.get(
        "/api/v1/runs",
        params={"workspace_id": workspace_id, "limit": 10},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [selected.id]


def test_run_api_rejects_incompatible_protocol(run_api) -> None:
    client, _state, workspace_id, _orchestrator = run_api

    response = client.post(
        "/api/v1/runs",
        json={
            "schema_version": "1.0.0",
            "workspace_id": workspace_id,
            "message": "inspect",
        },
    )

    assert response.status_code == 422


def test_run_api_persists_server_activated_policy_grant(run_api) -> None:
    client, state, workspace_id, _orchestrator = run_api

    response = client.post(
        "/api/v1/runs",
        json={
            "schema_version": "0.3.0",
            "workspace_id": workspace_id,
            "message": "supervised change",
            "policy": {
                "schema_version": "0.3.0",
                "mode": "supervised",
                "ttl_seconds": 120,
                "issued_at": "2000-01-01T00:00:00Z",
                "allowed_tools": ["write_file"],
                "path_globs": ["src/**"],
                "max_writes": 1,
            },
        },
    )

    assert response.status_code == 202
    policy = state.require_run(response.json()["id"]).metadata["run_policy"]
    assert policy["mode"] == "supervised"
    assert policy["issued_at"] != "2000-01-01T00:00:00Z"


def test_run_api_rejects_non_safe_policy_without_ttl(run_api) -> None:
    client, _state, workspace_id, _orchestrator = run_api

    response = client.post(
        "/api/v1/runs",
        json={
            "schema_version": "0.3.0",
            "workspace_id": workspace_id,
            "message": "unsafe grant",
            "policy": {
                "schema_version": "0.3.0",
                "mode": "autonomous",
                "allowed_tools": ["write_file"],
                "path_globs": ["**"],
                "max_writes": 1,
            },
        },
    )

    assert response.status_code == 422


def test_cancel_endpoint_cancels_persisted_queued_run(run_api) -> None:
    client, state, workspace_id, _orchestrator = run_api
    run = state.create_run(
        run_id=str(uuid4()),
        workspace_id=workspace_id,
        session_id=str(uuid4()),
        message="queued",
        metadata={},
    )

    response = client.post(f"/api/v1/runs/{run.id}/cancel")

    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert state.require_run(run.id).cancel_requested is True


def test_approval_decision_endpoint_rejects_pending_action(run_api) -> None:
    client, state, workspace_id, orchestrator = run_api
    run = state.create_run(
        run_id=str(uuid4()),
        workspace_id=workspace_id,
        session_id=str(uuid4()),
        message="mutate",
        metadata={},
    )
    state.transition_run(
        run_id=run.id,
        new_state=RunState.RUNNING,
        event_type="run_started",
    )
    state.transition_run(
        run_id=run.id,
        new_state=RunState.WAITING_APPROVAL,
        event_type="approval_required",
    )
    pending = orchestrator.approval_store.create(
        run_id=run.id,
        session_id=run.session_id,
        tool_call=ToolCall(
            id="write-call",
            name="write_file",
            arguments={"path": "result.txt", "content": "safe"},
        ),
        route="coding",
        project_path=None,
    )

    pending_response = client.get(f"/api/v1/approvals/{pending.approval_id}")
    assert pending_response.status_code == 200
    assert pending_response.json()["tool_name"] == "write_file"
    assert "arguments" not in pending_response.json()

    response = client.post(
        f"/api/v1/approvals/{pending.approval_id}/decision",
        json={
            "schema_version": "0.3.0",
            "approval_id": pending.approval_id,
            "decision": "reject",
            "preview_hash": pending.approval_hash,
            "decided_at": datetime.now(UTC).isoformat(),
        },
    )

    assert response.status_code == 202
    assert response.json()["state"] == "cancelled"
    assert state.get_approval_record(pending.approval_id)["state"] == "rejected"
