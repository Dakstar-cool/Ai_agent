from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.providers.memory.base import IMemoryService

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class JsonFileMemoryService(IMemoryService):
    def __init__(self, storage_path: str, recall_limit: int = 5) -> None:
        self.storage_path = Path(storage_path)
        self.recall_limit = recall_limit
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.touch(exist_ok=True)

    async def recall(self, query: str, session_id: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recall_sync, query, session_id)

    async def save(self, item: dict[str, Any], session_id: str | None = None) -> None:
        await asyncio.to_thread(self._save_sync, item, session_id)

    def _save_sync(self, item: dict[str, Any], session_id: str | None = None) -> None:
        record = {
            "session_id": session_id,
            "kind": item.get("kind", "interaction"),
            "user_message": item.get("user_message", ""),
            "assistant_reply": item.get("assistant_reply", ""),
            "route": item.get("route", "general"),
            "metadata": item.get("metadata", {}),
            "project_path": item.get("project_path"),
        }
        with self.storage_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _recall_sync(self, query: str, session_id: str | None = None) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        ranked: list[tuple[int, dict[str, Any]]] = []
        with self.storage_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if session_id and record.get("session_id") != session_id:
                    continue

                haystack = " ".join(
                    [
                        str(record.get("user_message", "")),
                        str(record.get("assistant_reply", "")),
                        str(record.get("route", "")),
                    ]
                )
                score = self._score(query_tokens, haystack)
                if score <= 0:
                    continue

                ranked.append(
                    (
                        score,
                        {
                            "summary": self._build_summary(record),
                            "score": score,
                            "route": record.get("route", "general"),
                            "session_id": record.get("session_id"),
                        },
                    )
                )

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[: self.recall_limit]]

    def _build_summary(self, record: dict[str, Any]) -> str:
        user_message = str(record.get("user_message", "")).strip()
        assistant_reply = str(record.get("assistant_reply", "")).strip()
        route = str(record.get("route", "general")).strip()

        reply_preview = assistant_reply[:180]
        if len(assistant_reply) > 180:
            reply_preview += "..."

        return f"[{route}] user={user_message} | assistant={reply_preview}"

    def _score(self, query_tokens: set[str], text: str) -> int:
        text_tokens = self._tokenize(text)
        return len(query_tokens & text_tokens)

    def _tokenize(self, text: str) -> set[str]:
        return {token.lower() for token in _TOKEN_RE.findall(text) if len(token) >= 3}
