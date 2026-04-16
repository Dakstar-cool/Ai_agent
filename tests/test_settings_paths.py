from __future__ import annotations

from pathlib import Path

from app.config.settings import Settings
from app.providers.memory.factory import build_memory_service
from app.providers.memory.json_file import JsonFileMemoryService


def test_settings_resolve_project_path_returns_absolute() -> None:
    settings = Settings(memory_file_path="data/memory/test.jsonl")
    resolved = settings.resolve_project_path(settings.memory_file_path)

    assert resolved.is_absolute()
    assert str(resolved).endswith(str(Path("data") / "memory" / "test.jsonl"))


def test_memory_factory_uses_resolved_absolute_path() -> None:
    settings = Settings(enable_memory=True, memory_backend="json", memory_file_path="data/memory/test.jsonl")
    service = build_memory_service(settings)

    assert isinstance(service, JsonFileMemoryService)
    assert service.storage_path.is_absolute()
    assert str(service.storage_path).endswith(str(Path("data") / "memory" / "test.jsonl"))
