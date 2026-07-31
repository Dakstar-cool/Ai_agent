from __future__ import annotations

import asyncio
import heapq
import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.providers.memory.base import IMemoryService
from app.providers.memory.models import (
    MemoryExportItem,
    MemoryRecallItem,
    MemoryRecallQuery,
    MemoryRecord,
    MemoryScopeQuery,
)

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class JsonFileMemoryService(IMemoryService):
    def __init__(
        self, storage_path: str, recall_limit: int = 5, max_recall_limit: int = 20
    ) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.max_recall_limit = max(1, max_recall_limit)
        self.recall_limit = min(max(1, recall_limit), self.max_recall_limit)
        self._lock = threading.Lock()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.touch(exist_ok=True)
        logger.info("json_memory_storage_ready path=%s", self.storage_path)

    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        return await asyncio.to_thread(self._recall_sync, query)

    async def save(self, item: MemoryRecord) -> None:
        await asyncio.to_thread(self._save_sync, item)

    async def export(self, query: MemoryScopeQuery) -> list[MemoryExportItem]:
        return await asyncio.to_thread(self._export_sync, query)

    async def delete(self, query: MemoryScopeQuery) -> int:
        return await asyncio.to_thread(self._delete_sync, query)

    def _save_sync(self, item: MemoryRecord) -> None:
        record = item.model_copy(
            update={
                "created_at": item.created_at or datetime.now(UTC).isoformat(),
                "project_path": self._normalize_path(item.project_path),
            }
        )
        with self._lock, self.storage_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
        logger.info(
            "memory_record_saved path=%s session_id=%s route=%s project_path=%s",
            self.storage_path,
            record.session_id,
            record.route,
            record.project_path,
        )

    def _recall_sync(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        query_tokens = self._tokenize(query.text)
        normalized_project_path = self._normalize_path(query.project_path)
        limit = self._coerce_limit(query.limit)

        if not query_tokens and not normalized_project_path and not query.session_id:
            return []

        ranked: list[tuple[int, str, int, MemoryRecallItem]] = []
        with self._lock:
            if not self.storage_path.exists():
                return []

            with self.storage_path.open("r", encoding="utf-8") as fh:
                self._scan_records(
                    fh, query, query_tokens, normalized_project_path, ranked, limit
                )

        top_ranked = heapq.nlargest(
            limit, ranked, key=lambda item: (item[0], item[1], item[2])
        )
        results = [item[3] for item in top_ranked]
        logger.info(
            "memory_recall_completed path=%s query_session_id=%s query_project_path=%s matched=%s",
            self.storage_path,
            query.session_id,
            normalized_project_path,
            len(results),
        )
        return results

    def _scan_records(
        self,
        fh,
        query: MemoryRecallQuery,
        query_tokens: set[str],
        normalized_project_path: str | None,
        ranked: list[tuple[int, str, int, MemoryRecallItem]],
        limit: int,
    ) -> None:
        counter = 0
        for line in fh:
            line = line.strip()
            if not line:
                continue

            record = self._parse_record(line)
            if record is None:
                continue

            score = self._calculate_score(
                query_tokens=query_tokens,
                record=record,
                session_id=query.session_id,
                project_path=normalized_project_path,
                route=query.route,
            )
            if score <= 0:
                continue

            counter += 1
            entry = (
                score,
                record.created_at or "",
                counter,
                MemoryRecallItem(
                    id=record.id,
                    summary=self._build_summary(record),
                    score=score,
                    kind=record.kind,
                    route=record.route,
                    session_id=record.session_id,
                    user_id=record.user_id,
                    project_id=record.project_id,
                    project_path=record.project_path,
                    provenance=record.provenance,
                    created_at=record.created_at or datetime.now(UTC).isoformat(),
                    expires_at=record.expires_at,
                ),
            )
            if len(ranked) < limit:
                heapq.heappush(ranked, entry)
            elif (entry[0], entry[1], entry[2]) > (
                ranked[0][0],
                ranked[0][1],
                ranked[0][2],
            ):
                heapq.heapreplace(ranked, entry)

    def _coerce_limit(self, limit: int | None) -> int:
        if limit is None:
            return self.recall_limit
        return min(max(1, limit), self.max_recall_limit)

    def _calculate_score(
        self,
        *,
        query_tokens: set[str],
        record: MemoryRecord,
        session_id: str | None,
        project_path: str | None,
        route: str | None,
    ) -> int:
        haystack = " ".join(
            [
                record.user_message,
                record.assistant_reply,
                record.route,
                json.dumps(record.metadata, ensure_ascii=False),
            ]
        )
        score = self._score(query_tokens, haystack)

        if project_path and record.project_path == project_path:
            score += 10
        if session_id and record.session_id == session_id:
            score += 6
        if route and record.route == route:
            score += 2

        return score

    def _build_summary(self, record: MemoryRecord) -> str:
        if record.summary.strip():
            return record.summary.strip()
        user_message = record.user_message.strip()
        assistant_reply = record.assistant_reply.strip()
        route = record.route.strip()

        reply_preview = assistant_reply[:180]
        if len(assistant_reply) > 180:
            reply_preview += "..."

        return f"[{route}] user={user_message} | assistant={reply_preview}"

    def _score(self, query_tokens: set[str], text: str) -> int:
        text_tokens = self._tokenize(text)
        return len(query_tokens & text_tokens)

    def _tokenize(self, text: str) -> set[str]:
        return {token.lower() for token in _TOKEN_RE.findall(text) if len(token) >= 3}

    def _normalize_path(self, value: str | None) -> str | None:
        if not value:
            return None
        return str(Path(value).expanduser()).replace("\\", "/").rstrip("/").lower()

    def _export_sync(self, query: MemoryScopeQuery) -> list[MemoryExportItem]:
        exported: list[MemoryExportItem] = []
        with self._lock, self.storage_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                record = self._parse_record(line)
                if record is None:
                    continue
                if not self._matches_scope(record, query):
                    continue
                exported.append(
                    MemoryExportItem(
                        id=record.id,
                        kind=record.kind,
                        summary=self._build_summary(record),
                        route=record.route,
                        session_id=record.session_id,
                        user_id=record.user_id,
                        project_id=record.project_id,
                        provenance=record.provenance,
                        created_at=record.created_at
                        or datetime.now(UTC).isoformat(),
                        expires_at=record.expires_at,
                    )
                )
        return exported

    def _delete_sync(self, query: MemoryScopeQuery) -> int:
        retained: list[str] = []
        deleted = 0
        with self._lock:
            with self.storage_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    record = self._parse_record(line)
                    if record is None:
                        retained.append(line)
                        continue
                    if self._matches_scope(record, query):
                        deleted += 1
                    else:
                        retained.append(line)
            temporary_path = self.storage_path.with_suffix(
                self.storage_path.suffix + ".tmp"
            )
            temporary_path.write_text("".join(retained), encoding="utf-8")
            temporary_path.replace(self.storage_path)
        return deleted

    @staticmethod
    def _matches_scope(record: MemoryRecord, query: MemoryScopeQuery) -> bool:
        if query.user_id and record.user_id != query.user_id:
            return False
        if query.project_id and record.project_id != query.project_id:
            return False
        return not (query.session_id and record.session_id != query.session_id)

    @staticmethod
    def _parse_record(line: str) -> MemoryRecord | None:
        try:
            return MemoryRecord.model_validate_json(line)
        except (TypeError, ValueError) as exc:
            logger.debug(
                "memory_record_skipped error_type=%s", exc.__class__.__name__
            )
            return None
