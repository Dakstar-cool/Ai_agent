from __future__ import annotations

import httpx
import pytest

from app.errors import LLMProviderUnavailableError
from app.providers.llm.lmstudio import LMStudioProvider


@pytest.mark.asyncio
async def test_lmstudio_provider_maps_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(self, url, json):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="demo")
    with pytest.raises(LLMProviderUnavailableError):
        await provider.chat(messages=[{"role": "user", "content": "hello"}])
