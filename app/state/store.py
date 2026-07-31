from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.errors import AppError
from app.runs.models import (
    ALLOWED_TRANSITIONS,
    RunEventRecord,
    RunRecord,
    RunState,
    WorkspaceRecord,
)


SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(UTC)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load_object(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("Expected a JSON object in worker state")
    return loaded


class SQLiteStateStore:
    """Small durable state store with one short-lived connection per operation."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._initialize_lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        root_path TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS session_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                        sequence INTEGER NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(session_id, sequence)
                    );

                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                        session_id TEXT NOT NULL REFERENCES sessions(id),
                        state TEXT NOT NULL,
                        message TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        result TEXT,
                        response_json TEXT,
                        error_json TEXT,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS run_events (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                        sequence INTEGER NOT NULL,
                        type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(run_id, sequence)
                    );

                    CREATE TABLE IF NOT EXISTS approvals (
                        id TEXT PRIMARY KEY,
                        run_id TEXT REFERENCES runs(id),
                        session_id TEXT NOT NULL REFERENCES sessions(id),
                        tool_call_json TEXT NOT NULL,
                        route TEXT NOT NULL,
                        project_path TEXT,
                        approval_hash TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('pending', 'consumed', 'rejected', 'expired')
                        ),
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        decided_at TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_session_messages_session_sequence
                        ON session_messages(session_id, sequence);
                    CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence
                        ON run_events(run_id, sequence);
                    CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
                    CREATE INDEX IF NOT EXISTS idx_approvals_session_state
                        ON approvals(session_id, state);
                    """
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def register_workspace(
        self, *, workspace_id: str, name: str, root_path: Path
    ) -> WorkspaceRecord:
        self.initialize()
        try:
            resolved = root_path.resolve(strict=True)
        except OSError as exc:
            raise AppError(
                message="Workspace root must be an existing directory",
                code="invalid_workspace_root",
                status_code=400,
            ) from exc
        if not resolved.is_dir():
            raise AppError(
                message="Workspace root must be an existing directory",
                code="invalid_workspace_root",
                status_code=400,
            )
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workspaces(id, name, root_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    root_path=excluded.root_path,
                    updated_at=excluded.updated_at
                """,
                (workspace_id, name, str(resolved), now, now),
            )
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            raise RuntimeError("Workspace registration did not persist")
        return workspace

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        return self._workspace_from_row(row) if row is not None else None

    def list_workspaces(self) -> list[WorkspaceRecord]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY created_at, id"
            ).fetchall()
        return [self._workspace_from_row(row) for row in rows]

    def get_or_create_session(self, session_id: str) -> list[dict[str, str]]:
        self.initialize()
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, created_at, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (session_id, now, now),
            )
            rows = connection.execute(
                """
                SELECT role, content FROM session_messages
                WHERE session_id=? ORDER BY sequence
                """,
                (session_id,),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def append_session_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        max_messages: int,
    ) -> None:
        self.initialize()
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(id, created_at, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (session_id, now, now),
            )
            sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM session_messages WHERE session_id=?
                """,
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO session_messages(session_id, sequence, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, sequence, role, content, now),
            )
            connection.execute(
                """
                DELETE FROM session_messages
                WHERE session_id=? AND id NOT IN (
                    SELECT id FROM session_messages WHERE session_id=?
                    ORDER BY sequence DESC LIMIT ?
                )
                """,
                (session_id, session_id, max(1, max_messages)),
            )

    def create_run(
        self,
        *,
        run_id: str,
        workspace_id: str,
        session_id: str,
        message: str,
        metadata: dict[str, Any],
    ) -> RunRecord:
        self.initialize()
        if self.get_workspace(workspace_id) is None:
            raise AppError(
                message="Workspace was not found",
                code="workspace_not_found",
                status_code=404,
                details={"workspace_id": workspace_id},
            )
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(id, created_at, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (session_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO runs(
                    id, workspace_id, session_id, state, message, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workspace_id,
                    session_id,
                    RunState.QUEUED.value,
                    message,
                    _dump(metadata),
                    now,
                    now,
                ),
            )
            self._append_event_connection(
                connection,
                run_id=run_id,
                event_type="run_created",
                payload={"state": RunState.QUEUED.value},
                created_at=now,
            )
        return self.require_run(run_id)

    def get_run(self, run_id: str) -> RunRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_from_row(row) if row is not None else None

    def require_run(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        if run is None:
            raise AppError(
                message="Run was not found",
                code="run_not_found",
                status_code=404,
                details={"run_id": run_id},
            )
        return run

    def transition_run(
        self,
        *,
        run_id: str,
        new_state: RunState,
        event_type: str,
        payload: dict[str, Any] | None = None,
        result: str | None = None,
        response_payload: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> RunRecord:
        self.initialize()
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise AppError(
                    message="Run was not found",
                    code="run_not_found",
                    status_code=404,
                    details={"run_id": run_id},
                )
            current = RunState(row["state"])
            if new_state != current and new_state not in ALLOWED_TRANSITIONS[current]:
                raise AppError(
                    message="Run state transition is not allowed",
                    code="invalid_run_transition",
                    status_code=409,
                    details={
                        "run_id": run_id,
                        "current_state": current.value,
                        "requested_state": new_state.value,
                    },
                )
            connection.execute(
                """
                UPDATE runs SET state=?, result=COALESCE(?, result),
                    response_json=COALESCE(?, response_json),
                    error_json=COALESCE(?, error_json), updated_at=?
                WHERE id=?
                """,
                (
                    new_state.value,
                    result,
                    _dump(response_payload) if response_payload is not None else None,
                    _dump(error) if error is not None else None,
                    now,
                    run_id,
                ),
            )
            self._append_event_connection(
                connection,
                run_id=run_id,
                event_type=event_type,
                payload={"state": new_state.value, **(payload or {})},
                created_at=now,
            )
        return self.require_run(run_id)

    def append_run_event(
        self, *, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> RunEventRecord:
        self.initialize()
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._append_event_connection(
                connection,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                created_at=now,
            )

    def list_run_events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> list[RunEventRecord]:
        self.initialize()
        self.require_run(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_events WHERE run_id=? AND sequence>?
                ORDER BY sequence
                """,
                (run_id, max(0, after_sequence)),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def request_cancel(self, run_id: str) -> RunRecord:
        self.initialize()
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? WHERE id=?",
                (utc_now().isoformat(), run_id),
            ).rowcount
        if not changed:
            return self.require_run(run_id)
        return self.require_run(run_id)

    def incomplete_runs(self) -> list[RunRecord]:
        self.initialize()
        terminal = tuple(state.value for state in RunState if state.terminal)
        placeholders = ",".join("?" for _ in terminal)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM runs WHERE state NOT IN ({placeholders}) ORDER BY created_at",
                terminal,
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def create_approval_record(
        self,
        *,
        approval_id: str,
        run_id: str | None,
        session_id: str,
        tool_call: dict[str, Any],
        route: str,
        project_path: str | None,
        approval_hash: str,
        created_at: datetime,
        expires_at: datetime,
        max_pending: int,
    ) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_approvals_connection(connection, created_at)
            existing = connection.execute(
                """
                SELECT * FROM approvals
                WHERE session_id=? AND route=?
                    AND COALESCE(project_path, '')=COALESCE(?, '')
                    AND approval_hash=? AND state='pending'
                ORDER BY created_at LIMIT 1
                """,
                (session_id, route, project_path, approval_hash),
            ).fetchone()
            if existing is not None:
                return self._approval_from_row(existing)

            pending_count = connection.execute(
                "SELECT COUNT(*) FROM approvals WHERE state='pending'"
            ).fetchone()[0]
            if pending_count >= max(1, max_pending):
                oldest = connection.execute(
                    """
                    SELECT id FROM approvals WHERE state='pending'
                    ORDER BY created_at, id LIMIT 1
                    """
                ).fetchone()
                if oldest is not None:
                    connection.execute(
                        """
                        UPDATE approvals SET state='expired', decided_at=? WHERE id=?
                        """,
                        (created_at.isoformat(), oldest["id"]),
                    )

            connection.execute(
                """
                INSERT INTO approvals(
                    id, run_id, session_id, tool_call_json, route, project_path,
                    approval_hash, state, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    run_id,
                    session_id,
                    _dump(tool_call),
                    route,
                    project_path,
                    approval_hash,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        record = self.get_approval_record(approval_id)
        if record is None:
            raise RuntimeError("Approval creation did not persist")
        return record

    def get_approval_record(self, approval_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
        return self._approval_from_row(row) if row is not None else None

    def consume_approval_record(
        self, *, approval_id: str, session_id: str, now: datetime
    ) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approvals WHERE id=? AND session_id=?",
                (approval_id, session_id),
            ).fetchone()
            if row is None or row["state"] != "pending":
                raise AppError(
                    message="Pending approval was not found for this session",
                    code="approval_not_found",
                    status_code=404,
                )
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    """
                    UPDATE approvals SET state='expired', decided_at=? WHERE id=?
                    """,
                    (now.isoformat(), approval_id),
                )
                raise AppError(
                    message="Pending approval has expired",
                    code="approval_expired",
                    status_code=409,
                )
            connection.execute(
                """
                UPDATE approvals SET state='consumed', decided_at=? WHERE id=?
                """,
                (now.isoformat(), approval_id),
            )
        return self._approval_from_row(row)

    def reject_approval_record(
        self, *, approval_id: str, expected_hash: str, now: datetime
    ) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None or row["state"] != "pending":
                raise AppError(
                    message="Pending approval was not found",
                    code="approval_not_found",
                    status_code=404,
                )
            if row["approval_hash"] != expected_hash:
                raise AppError(
                    message="Approval hash does not match the pending action",
                    code="approval_hash_mismatch",
                    status_code=409,
                )
            connection.execute(
                """
                UPDATE approvals SET state='rejected', decided_at=? WHERE id=?
                """,
                (now.isoformat(), approval_id),
            )
        return self._approval_from_row(row)

    def validate_approval_hash(self, *, approval_id: str, expected_hash: str) -> None:
        record = self.get_approval_record(approval_id)
        if record is None or record["state"] != "pending":
            raise AppError(
                message="Pending approval was not found",
                code="approval_not_found",
                status_code=404,
            )
        if record["approval_hash"] != expected_hash:
            raise AppError(
                message="Approval hash does not match the pending action",
                code="approval_hash_mismatch",
                status_code=409,
            )

    def bind_approval_to_run(self, *, approval_id: str, run_id: str) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE approvals SET run_id=? WHERE id=? AND state='pending'
                """,
                (run_id, approval_id),
            )

    def _append_event_connection(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> RunEventRecord:
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        event_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO run_events(id, run_id, sequence, type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, run_id, sequence, event_type, _dump(payload), created_at),
        )
        return RunEventRecord(
            id=event_id,
            run_id=run_id,
            sequence=sequence,
            type=event_type,
            payload=payload,
            created_at=datetime.fromisoformat(created_at),
        )

    def _run_from_row(self, row: sqlite3.Row) -> RunRecord:
        metadata = _load_object(row["metadata_json"])
        if metadata is None:
            metadata = {}
        return RunRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            state=RunState(row["state"]),
            message=row["message"],
            metadata=metadata,
            result=row["result"],
            response_payload=_load_object(row["response_json"]),
            error=_load_object(row["error_json"]),
            cancel_requested=bool(row["cancel_requested"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _event_from_row(self, row: sqlite3.Row) -> RunEventRecord:
        payload = _load_object(row["payload_json"])
        if payload is None:
            payload = {}
        return RunEventRecord(
            id=row["id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            type=row["type"],
            payload=payload,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _workspace_from_row(self, row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=row["id"],
            name=row["name"],
            root_path=row["root_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _approval_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        tool_call = _load_object(row["tool_call_json"])
        if tool_call is None:
            raise ValueError("Stored approval has no tool call")
        return {
            "approval_id": row["id"],
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "tool_call": tool_call,
            "route": row["route"],
            "project_path": row["project_path"],
            "approval_hash": row["approval_hash"],
            "state": row["state"],
            "created_at": datetime.fromisoformat(row["created_at"]),
            "expires_at": datetime.fromisoformat(row["expires_at"]),
            "decided_at": (
                datetime.fromisoformat(row["decided_at"])
                if row["decided_at"] is not None
                else None
            ),
        }

    def _expire_approvals_connection(
        self, connection: sqlite3.Connection, now: datetime
    ) -> None:
        connection.execute(
            """
            UPDATE approvals SET state='expired', decided_at=?
            WHERE state='pending' AND expires_at<=?
            """,
            (now.isoformat(), now.isoformat()),
        )
