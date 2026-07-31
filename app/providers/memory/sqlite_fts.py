from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.providers.memory.base import IMemoryService
from app.providers.memory.models import (
    MemoryExportItem,
    MemoryRecallItem,
    MemoryRecallQuery,
    MemoryRecord,
    MemoryScopeQuery,
)
from app.providers.memory.policy import contains_sensitive_data

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class SQLiteFTSMemoryService(IMemoryService):
    """Local persistent knowledge store that never persists raw conversation traces."""

    def __init__(
        self,
        storage_path: str,
        *,
        recall_limit: int = 5,
        max_recall_limit: int = 20,
        ttl_days: int = 90,
    ) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_recall_limit = max(1, max_recall_limit)
        self.recall_limit = min(max(1, recall_limit), self.max_recall_limit)
        self.ttl_days = max(1, ttl_days)
        self._initialize()

    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        return await asyncio.to_thread(self._recall_sync, query)

    async def save(self, item: MemoryRecord) -> None:
        await asyncio.to_thread(self._save_sync, item)

    async def export(self, query: MemoryScopeQuery) -> list[MemoryExportItem]:
        return await asyncio.to_thread(self._export_sync, query)

    async def delete(self, query: MemoryScopeQuery) -> int:
        return await asyncio.to_thread(self._delete_sync, query)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.storage_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    route TEXT NOT NULL,
                    session_id TEXT,
                    user_id TEXT,
                    project_id TEXT,
                    provenance_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memory_scope
                    ON memory_records(user_id, project_id, session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_expiry
                    ON memory_records(expires_at);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    summary,
                    route,
                    content='memory_records',
                    content_rowid='rowid',
                    tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS memory_records_ai AFTER INSERT ON memory_records BEGIN
                    INSERT INTO memory_fts(rowid, summary, route)
                    VALUES (new.rowid, new.summary, new.route);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_records_ad AFTER DELETE ON memory_records BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, summary, route)
                    VALUES ('delete', old.rowid, old.summary, old.route);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_records_au AFTER UPDATE ON memory_records BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, summary, route)
                    VALUES ('delete', old.rowid, old.summary, old.route);
                    INSERT INTO memory_fts(rowid, summary, route)
                    VALUES (new.rowid, new.summary, new.route);
                END;
                """
            )

    def _save_sync(self, item: MemoryRecord) -> None:
        summary = self._summary_for(item)
        provenance = {
            str(key)[:100]: str(value)[:500] for key, value in item.provenance.items()
        }
        if contains_sensitive_data(summary) or contains_sensitive_data(provenance):
            raise ValueError("Persistent memory contains protected data")

        created_at = item.created_at or datetime.now(UTC).isoformat()
        expires_at = (
            item.expires_at
            or (datetime.now(UTC) + timedelta(days=self.ttl_days)).isoformat()
        )
        project_id = item.project_id or self._project_id_for_path(item.project_path)
        with self._connect() as connection:
            self._purge_expired(connection)
            connection.execute(
                """
                INSERT INTO memory_records (
                    id, kind, summary, route, session_id, user_id, project_id,
                    provenance_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    summary=excluded.summary,
                    route=excluded.route,
                    session_id=excluded.session_id,
                    user_id=excluded.user_id,
                    project_id=excluded.project_id,
                    provenance_json=excluded.provenance_json,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    item.id,
                    item.kind,
                    summary,
                    item.route,
                    item.session_id,
                    item.user_id,
                    project_id,
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    created_at,
                    expires_at,
                ),
            )

    def _recall_sync(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        limit = self._coerce_limit(query.limit)
        project_id = query.project_id or self._project_id_for_path(query.project_path)
        conditions, parameters = self._scope_conditions(
            user_id=query.user_id,
            project_id=project_id,
            session_id=query.session_id,
        )
        if query.route:
            conditions.append("r.route = ?")
            parameters.append(query.route)
        now = datetime.now(UTC).isoformat()
        conditions.append("(r.expires_at IS NULL OR r.expires_at > ?)")
        parameters.append(now)

        match_query = self._match_query(query.text)
        with self._connect() as connection:
            self._purge_expired(connection)
            if match_query:
                sql = f"""
                    SELECT r.*, bm25(memory_fts) AS relevance
                    FROM memory_fts
                    JOIN memory_records AS r ON r.rowid = memory_fts.rowid
                    WHERE memory_fts MATCH ? AND {" AND ".join(conditions)}
                    ORDER BY relevance, r.created_at DESC
                    LIMIT ?
                """
                rows = connection.execute(
                    sql, [match_query, *parameters, limit]
                ).fetchall()
            elif any((query.user_id, project_id, query.session_id)):
                sql = f"""
                    SELECT r.*, 0.0 AS relevance
                    FROM memory_records AS r
                    WHERE {" AND ".join(conditions)}
                    ORDER BY r.created_at DESC
                    LIMIT ?
                """
                rows = connection.execute(sql, [*parameters, limit]).fetchall()
            else:
                return []
        return [self._recall_item(row) for row in rows]

    def _export_sync(self, query: MemoryScopeQuery) -> list[MemoryExportItem]:
        conditions, parameters = self._scope_conditions(
            user_id=query.user_id,
            project_id=query.project_id,
            session_id=query.session_id,
        )
        conditions.append("(expires_at IS NULL OR expires_at > ?)")
        parameters.append(datetime.now(UTC).isoformat())
        with self._connect() as connection:
            self._purge_expired(connection)
            rows = connection.execute(
                f"""
                SELECT * FROM memory_records
                WHERE {" AND ".join(conditions)}
                ORDER BY created_at, id
                LIMIT 10000
                """,
                parameters,
            ).fetchall()
        return [self._export_item(row) for row in rows]

    def _delete_sync(self, query: MemoryScopeQuery) -> int:
        conditions, parameters = self._scope_conditions(
            user_id=query.user_id,
            project_id=query.project_id,
            session_id=query.session_id,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM memory_records WHERE {' AND '.join(conditions)}",
                parameters,
            )
            return max(0, cursor.rowcount)

    @staticmethod
    def _scope_conditions(
        *, user_id: str | None, project_id: str | None, session_id: str | None
    ) -> tuple[list[str], list[str]]:
        conditions: list[str] = []
        parameters: list[str] = []
        for column, value in (
            ("user_id", user_id),
            ("project_id", project_id),
            ("session_id", session_id),
        ):
            if value:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        return conditions or ["1 = 0"], parameters

    @staticmethod
    def _purge_expired(connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM memory_records WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (datetime.now(UTC).isoformat(),),
        )

    def _coerce_limit(self, value: int | None) -> int:
        if value is None:
            return self.recall_limit
        return min(max(1, value), self.max_recall_limit)

    @staticmethod
    def _match_query(text: str) -> str:
        tokens = [
            token.casefold() for token in _TOKEN_RE.findall(text) if len(token) >= 2
        ]
        unique_tokens = list(dict.fromkeys(tokens))[:20]
        return " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in unique_tokens
        )

    @staticmethod
    def _summary_for(item: MemoryRecord) -> str:
        if item.summary.strip():
            return item.summary.strip()[:4_000]
        message = item.user_message.strip()[:1_200]
        decision = item.assistant_reply.strip()[:2_400]
        return f"Request: {message}\nDecision: {decision}"[:4_000]

    @staticmethod
    def _project_id_for_path(project_path: str | None) -> str | None:
        if not project_path:
            return None
        normalized = (
            str(Path(project_path).expanduser())
            .replace("\\", "/")
            .rstrip("/")
            .casefold()
        )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return f"local-{digest}"

    @staticmethod
    def _load_provenance(row: sqlite3.Row) -> dict[str, str]:
        try:
            value: Any = json.loads(row["provenance_json"])
        except (TypeError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    def _recall_item(self, row: sqlite3.Row) -> MemoryRecallItem:
        relevance = abs(float(row["relevance"] or 0.0))
        score = max(1, round(1_000 / (1.0 + relevance)))
        return MemoryRecallItem(
            id=row["id"],
            kind=row["kind"],
            summary=row["summary"],
            score=score,
            route=row["route"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            project_id=row["project_id"],
            provenance=self._load_provenance(row),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def _export_item(self, row: sqlite3.Row) -> MemoryExportItem:
        return MemoryExportItem(
            id=row["id"],
            kind=row["kind"],
            summary=row["summary"],
            route=row["route"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            project_id=row["project_id"],
            provenance=self._load_provenance(row),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )
