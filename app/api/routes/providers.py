from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes.chat import require_api_key
from app.config.settings import get_settings
from app.providers.llm.factory import build_llm_provider
from app.providers.llm.models import ProviderCapabilities
from app.providers.llm.runtime_config import ProviderRuntimeConfig, get_runtime_provider

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/providers/capabilities", response_model=ProviderCapabilities)
async def provider_capabilities() -> ProviderCapabilities:
    settings = get_settings()
    config = get_runtime_provider() or ProviderRuntimeConfig(
        base_url=settings.lmstudio_base_url,
        model=settings.lmstudio_model,
    )
    provider = build_llm_provider(config)
    try:
        return await provider.discover_capabilities()
    finally:
        await provider.aclose()
