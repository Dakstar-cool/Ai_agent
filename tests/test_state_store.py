from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from app.errors import AppError
from app.orchestrator.session.manager import SessionManager
from app.runs.models import RunState
from app.state.store import SQLiteStateStore


def _store(tmp_path) -> tuple[SQLiteStateStore, str]:
    store = SQLiteStateStore(tmp_path / "state" / "worker.sqlite3")
    workspace_id = str(uuid4())
    store.register_workspace(
        workspace_id=workspace_id,
        name="Test workspace",
        root_path=tmp_path,
    )
    return store, workspace_id


def test_store_enables_wal_and_persists_run_across_instances(tmp_path) -> None:
    store, workspace_id = _store(tmp_path)
    run_id = str(uuid4())
    session_id = str(uuid4())
    store.create_run(
        run_id=run_id,
        workspace_id=workspace_id,
        session_id=session_id,
        message="inspect project",
        metadata={"source": "test"},
    )
    store.transition_run(
        run_id=run_id,
        new_state=RunState.RUNNING,
        event_type="run_started",
    )

    reopened = SQLiteStateStore(store.path)
    persisted = reopened.require_run(run_id)

    assert persisted.state is RunState.RUNNING
    assert persisted.metadata == {"source": "test"}
    assert [event.sequence for event in reopened.list_run_events(run_id)] == [1, 2]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_store_lists_recent_runs_by_workspace(tmp_path) -> None:
    store, workspace_a = _store(tmp_path)
    workspace_b = str(uuid4())
    other_root = tmp_path / "other"
    other_root.mkdir()
    store.register_workspace(
        workspace_id=workspace_b,
        name="Other workspace",
        root_path=other_root,
    )
    first = store.create_run(
        run_id=str(uuid4()),
        workspace_id=workspace_a,
        session_id=str(uuid4()),
        message="first",
        metadata={},
    )
    second = store.create_run(
        run_id=str(uuid4()),
        workspace_id=workspace_b,
        session_id=str(uuid4()),
        message="second",
        metadata={},
    )

    assert [run.id for run in store.list_runs(limit=1)] == [second.id]
    assert [run.id for run in store.list_runs(workspace_id=workspace_a)] == [first.id]


def test_invalid_run_transition_is_rejected_without_event(tmp_path) -> None:
    store, workspace_id = _store(tmp_path)
    run_id = str(uuid4())
    store.create_run(
        run_id=run_id,
        workspace_id=workspace_id,
        session_id=str(uuid4()),
        message="test",
        metadata={},
    )

    with pytest.raises(AppError) as error:
        store.transition_run(
            run_id=run_id,
            new_state=RunState.COMPLETED,
            event_type="run_completed",
        )

    assert error.value.code == "invalid_run_transition"
    assert len(store.list_run_events(run_id)) == 1


def test_session_history_is_persistent_and_bounded(tmp_path) -> None:
    store, _ = _store(tmp_path)
    session_id = str(uuid4())

    for index in range(4):
        store.append_session_message(
            session_id=session_id,
            role="user",
            content=f"message-{index}",
            max_messages=3,
        )

    reopened = SQLiteStateStore(store.path)
    history = reopened.get_or_create_session(session_id)

    assert [message["content"] for message in history] == [
        "message-1",
        "message-2",
        "message-3",
    ]


def test_session_manager_recovers_history_after_restart(tmp_path) -> None:
    store, _ = _store(tmp_path)
    first = SessionManager(max_messages=3, state_store=store)
    first.append_message("persistent-session", "user", "hello")

    reopened_store = SQLiteStateStore(store.path)
    second = SessionManager(max_messages=3, state_store=reopened_store)

    assert second.get_or_create("persistent-session").history == [
        {"role": "user", "content": "hello"}
    ]


def test_unknown_workspace_cannot_scope_a_run(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "worker.sqlite3")

    with pytest.raises(AppError) as error:
        store.create_run(
            run_id=str(uuid4()),
            workspace_id=str(uuid4()),
            session_id=str(uuid4()),
            message="test",
            metadata={},
        )

    assert error.value.code == "workspace_not_found"
