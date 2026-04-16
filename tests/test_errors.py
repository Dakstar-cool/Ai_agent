from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.routes.chat import get_orchestrator
from app.errors import LLMProviderUnavailableError
from app.main import create_app


def test_llm_unavailable_returns_503() -> None:
    app = create_app()
    orchestrator = get_orchestrator()
    original_handle = orchestrator.handle
    orchestrator.handle = AsyncMock(side_effect=LLMProviderUnavailableError())

    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/chat",
            json={"message": "test message", "session_id": "test-session"},
        )
    finally:
        orchestrator.handle = original_handle

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "llm_backend_unavailable"
    assert "request_id" in payload["error"]
