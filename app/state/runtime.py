from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.config.settings import get_settings
from app.runs.models import WorkspaceRecord
from app.state.store import SQLiteStateStore


def workspace_id_for_path(path: Path) -> str:
    normalized = str(path.resolve()).replace("\\", "/").casefold()
    return str(uuid5(NAMESPACE_URL, f"ai-agent-workspace:{normalized}"))


@lru_cache(maxsize=1)
def get_state_store() -> SQLiteStateStore:
    settings = get_settings()
    database_path = settings.resolve_state_db_path()
    store = SQLiteStateStore(database_path)
    store.initialize()
    return store


def get_default_workspace() -> WorkspaceRecord:
    settings = get_settings()
    root = settings.resolve_project_path(settings.tool_workspace_root).resolve()
    return get_state_store().register_workspace(
        workspace_id=workspace_id_for_path(root),
        name="Default Workspace",
        root_path=root,
    )


def clear_state_store_cache() -> None:
    get_state_store.cache_clear()
