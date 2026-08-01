from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes.chat import require_api_key
from app.config.settings import get_settings
from app.providers.memory.factory import build_memory_service
from app.schemas.memory import (
    MemoryDeleteResponse,
    MemoryExportResponse,
    MemoryScopeRequest,
)

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/memory/export", response_model=MemoryExportResponse)
async def export_memory(request: MemoryScopeRequest) -> MemoryExportResponse:
    service = build_memory_service(get_settings())
    return MemoryExportResponse(items=await service.export(request.to_query()))


@router.delete("/memory", response_model=MemoryDeleteResponse)
async def delete_memory(request: MemoryScopeRequest) -> MemoryDeleteResponse:
    service = build_memory_service(get_settings())
    return MemoryDeleteResponse(deleted=await service.delete(request.to_query()))
