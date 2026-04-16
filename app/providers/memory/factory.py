from __future__ import annotations

import logging

from app.config.settings import Settings
from app.providers.memory.base import IMemoryService
from app.providers.memory.json_file import JsonFileMemoryService
from app.providers.memory.noop import NoOpMemoryService

logger = logging.getLogger(__name__)


def build_memory_service(settings: Settings) -> IMemoryService:
    if not settings.enable_memory:
        logger.info("memory_service_initialized backend=noop enabled=false")
        return NoOpMemoryService()

    if settings.memory_backend == "json":
        resolved_storage_path = settings.resolve_project_path(settings.memory_file_path)
        logger.info(
            "memory_service_initialized backend=json enabled=true storage_path=%s recall_limit=%s",
            resolved_storage_path,
            settings.memory_recall_limit,
        )
        return JsonFileMemoryService(
            storage_path=str(resolved_storage_path),
            recall_limit=settings.memory_recall_limit,
        )

    logger.warning("memory_service_initialized backend=noop enabled=true unsupported_backend=%s", settings.memory_backend)
    return NoOpMemoryService()
