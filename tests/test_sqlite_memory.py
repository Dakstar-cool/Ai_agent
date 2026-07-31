from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.providers.memory.models import (
    MemoryRecallQuery,
    MemoryRecord,
    MemoryScopeQuery,
)
from app.providers.memory.sqlite_fts import SQLiteFTSMemoryService


@pytest.mark.asyncio
async def test_sqlite_memory_recalls_only_matching_scope(tmp_path) -> None:
    service = SQLiteFTSMemoryService(str(tmp_path / "memory.sqlite3"))
    await service.save(
        MemoryRecord(
            summary="Decision: use SQLite FTS5 for project search",
            project_id="project-a",
            user_id="user-a",
            session_id="session-a",
            provenance={"source": "test"},
        )
    )
    await service.save(
        MemoryRecord(
            summary="Decision: use a different backend",
            project_id="project-b",
            user_id="user-a",
            session_id="session-b",
        )
    )

    recalled = await service.recall(
        MemoryRecallQuery(text="SQLite search", project_id="project-a")
    )

    assert [item.project_id for item in recalled] == ["project-a"]
    assert recalled[0].summary == "Decision: use SQLite FTS5 for project search"
    assert recalled[0].provenance == {"source": "test"}


@pytest.mark.asyncio
async def test_sqlite_memory_export_omits_raw_trace_and_delete_is_scoped(
    tmp_path,
) -> None:
    storage_path = tmp_path / "memory.sqlite3"
    service = SQLiteFTSMemoryService(str(storage_path))
    await service.save(
        MemoryRecord(
            summary="Approved the local-only provider decision",
            user_message="raw prompt must not be stored",
            assistant_reply="raw response must not be stored",
            project_id="project-a",
            session_id="session-a",
        )
    )
    await service.save(
        MemoryRecord(summary="Keep this", project_id="project-b")
    )

    exported = await service.export(MemoryScopeQuery(project_id="project-a"))
    deleted = await service.delete(MemoryScopeQuery(project_id="project-a"))

    assert len(exported) == 1
    assert "raw prompt" not in exported[0].model_dump_json()
    assert "raw response" not in exported[0].model_dump_json()
    assert deleted == 1
    assert await service.export(MemoryScopeQuery(project_id="project-a")) == []
    assert len(await service.export(MemoryScopeQuery(project_id="project-b"))) == 1

    with sqlite3.connect(storage_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(memory_records)")
        }
    assert "user_message" not in columns
    assert "assistant_reply" not in columns


@pytest.mark.asyncio
async def test_sqlite_memory_purges_expired_records(tmp_path) -> None:
    service = SQLiteFTSMemoryService(str(tmp_path / "memory.sqlite3"))
    await service.save(
        MemoryRecord(
            summary="Expired knowledge",
            project_id="project-a",
            expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
    )

    recalled = await service.recall(
        MemoryRecallQuery(text="Expired", project_id="project-a")
    )
    exported = await service.export(MemoryScopeQuery(project_id="project-a"))

    assert recalled == []
    assert exported == []


@pytest.mark.asyncio
async def test_sqlite_memory_rejects_secrets(tmp_path) -> None:
    service = SQLiteFTSMemoryService(str(tmp_path / "memory.sqlite3"))

    with pytest.raises(ValueError, match="protected data"):
        await service.save(
            MemoryRecord(
                summary="Authorization: Bearer do-not-store",
                project_id="project-a",
            )
        )
