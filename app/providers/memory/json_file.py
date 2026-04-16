from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from app.providers.memory.base import IMemoryService
from app.providers.memory.models import MemoryRecallItem, MemoryRecallQuery, MemoryRecord

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class JsonFileMemoryService(IMemoryService):
    def __init__(self, storage_path: str, recall_limit: int = 5) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.recall_limit = recall_limit
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.touch(exist_ok=True)
        logger.info("json_memory_storage_ready path=%s", self.storage_path)

    async def recall(self, query: MemoryRecallQuery) -> list[MemoryRecallItem]:
        return await asyncio.to_thread(self._recall_sync, query)

    async def save(self, item: MemoryRecord) -> None:
        await asyncio.to_thread(self._save_sync, item)

    def _save_sync(self, item: MemoryRecord) -> None:
        record = item.model_copy(
            update={
                "created_at": item.created_at or datetime.now(UTC).isoformat(),
                "project_path": self._normalize_path(item.project_path),
            }
        )
        with self.storage_path.open("a", encoding="utf-8") as fh:
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
        limit = query.limit or self.recall_limit

        if not query_tokens and not normalized_project_path and not query.session_id:
            return []

        ranked: list[tuple[int, str, MemoryRecallItem]] = []
        with self.storage_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue

                try:
                    raw_record = json.loads(line)
                    record = MemoryRecord.model_validate(raw_record)
                except Exception:
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

                ranked.append((
                    score,
                    record.created_at or "",
                    MemoryRecallItem(
                        summary=self._build_summary(record),
                        score=score,
                        route=record.route,
                        session_id=record.session_id,
                        project_path=record.project_path,
                        created_at=record.created_at or datetime.now(UTC).isoformat(),
                    ),
                ))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results = [item[2] for item in ranked[:limit]]
        logger.info(
            "memory_recall_completed path=%s query_session_id=%s query_project_path=%s matched=%s",
            self.storage_path,
            query.session_id,
            normalized_project_path,
            len(results),
        )
        return results

    def _calculate_score(
        self,
        *,
        query_tokens: set[str],
        record: MemoryRecord,
        session_id: str | None,
        project_path: str | None,
        route: str | None,
    ) -> int:
        haystack = " ".join([
            record.user_message,
            record.assistant_reply,
            record.route,
            json.dumps(record.metadata, ensure_ascii=False),
        ])
        score = self._score(query_tokens, haystack)

        if project_path and record.project_path == project_path:
            score += 10
        if session_id and record.session_id == session_id:
            score += 6
        if route and record.route == route:
            score += 2

        return score

    def _build_summary(self, record: MemoryRecord) -> str:
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
