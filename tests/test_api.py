from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import _rate_limit_allows_request, create_app
from app.schemas.chat import ChatResponse


class FakeOrchestrator:
    async def handle(self, request):
        return ChatResponse(
            session_id=request.session_id or "test-session",
            route="general",
            reply="ok",
            steps=[],
        )


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import app.api.routes.chat as chat_routes

    monkeypatch.setattr(chat_routes, "get_orchestrator", lambda: FakeOrchestrator())
    return TestClient(create_app())


def test_health_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_works_without_api_key_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "")
    client = _client(monkeypatch)

    response = client.post("/api/v1/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert response.json()["reply"] == "ok"


def test_chat_requires_api_key_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    client = _client(monkeypatch)

    response = client.post("/api/v1/chat", json={"message": "hello"})

    assert response.status_code == 401


def test_chat_accepts_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    client = _client(monkeypatch)

    response = client.post(
        "/api/v1/chat",
        headers={"X-API-Key": "secret-key"},
        json={"message": "hello"},
    )

    assert response.status_code == 200


def test_chat_accepts_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    client = _client(monkeypatch)

    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": "Bearer secret-key"},
        json={"message": "hello"},
    )

    assert response.status_code == 200


def test_chat_rejects_invalid_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    client = _client(monkeypatch)

    response = client.post(
        "/api/v1/chat",
        headers={"X-API-Key": "wrong"},
        json={"message": "hello"},
    )

    assert response.status_code == 401


def test_rate_limit_allows_requests_until_limit() -> None:
    state: dict[str, tuple[float, int]] = {}

    assert _rate_limit_allows_request(state, "client", limit=2, now=1.0) is True
    assert _rate_limit_allows_request(state, "client", limit=2, now=2.0) is True


def test_rate_limit_rejects_after_limit() -> None:
    state: dict[str, tuple[float, int]] = {}

    assert _rate_limit_allows_request(state, "client", limit=1, now=1.0) is True
    assert _rate_limit_allows_request(state, "client", limit=1, now=2.0) is False


def test_rate_limit_resets_after_window() -> None:
    state: dict[str, tuple[float, int]] = {}

    assert (
        _rate_limit_allows_request(
            state, "client", limit=1, now=1.0, window_seconds=10.0
        )
        is True
    )
    assert (
        _rate_limit_allows_request(
            state, "client", limit=1, now=12.0, window_seconds=10.0
        )
        is True
    )
