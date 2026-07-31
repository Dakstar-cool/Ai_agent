from __future__ import annotations

import httpx
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
    assert response.json()["version"] == "0.8.1"
    assert response.json()["protocol_version"] == "0.3.0"


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


def test_provider_capabilities_are_exposed_without_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self, url):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"data": [{"id": "demo", "context_length": 4096}]},
        )

    monkeypatch.setenv("LMSTUDIO_MODEL", "demo")
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    client = _client(monkeypatch)

    response = client.get("/api/v1/providers/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "0.3.0",
        "provider": "lmstudio",
        "model": "demo",
        "tools": True,
        "streaming": True,
        "context_limit": 4096,
        "available_models": ["demo"],
        "discovered": True,
    }
    assert "api_key" not in response.text.casefold()


def test_memory_export_and_delete_require_explicit_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("ENABLE_MEMORY", "true")
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("MEMORY_SQLITE_PATH", str(tmp_path / "memory.sqlite3"))
    client = _client(monkeypatch)

    invalid = client.post(
        "/api/v1/memory/export", json={"schema_version": "0.3.0"}
    )
    exported = client.post(
        "/api/v1/memory/export",
        json={"schema_version": "0.3.0", "project_id": "project-a"},
    )
    deleted = client.request(
        "DELETE",
        "/api/v1/memory",
        json={"schema_version": "0.3.0", "project_id": "project-a"},
    )

    assert invalid.status_code == 422
    assert exported.json() == {"schema_version": "0.3.0", "items": []}
    assert deleted.json() == {"schema_version": "0.3.0", "deleted": 0}
