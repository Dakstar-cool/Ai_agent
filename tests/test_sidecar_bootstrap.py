from __future__ import annotations

import base64
import json
from io import StringIO

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security import configure_bootstrap_token
from app.sidecar import create_loopback_socket, read_bootstrap

TOKEN = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")


def test_sidecar_bootstrap_accepts_only_256_bit_token_and_loopback() -> None:
    config = read_bootstrap(
        StringIO(json.dumps({"token": TOKEN, "host": "127.0.0.1", "port": 0}) + "\n")
    )

    assert config.token == TOKEN
    assert config.host == "127.0.0.1"
    with create_loopback_socket(config) as server_socket:
        host, port = server_socket.getsockname()
        assert host == "127.0.0.1"
        assert port > 0


def test_sidecar_bootstrap_accepts_explicit_https_remote_provider() -> None:
    config = read_bootstrap(
        StringIO(
            json.dumps(
                {
                    "token": TOKEN,
                    "provider": {
                        "baseUrl": "https://llm.example.invalid/v1",
                        "model": "example/model",
                        "apiKey": "secret-in-memory-only",
                        "remoteOptIn": True,
                    },
                }
            )
            + "\n"
        )
    )

    assert config.provider is not None
    assert config.provider.requires_network_permission is True
    assert "secret-in-memory-only" not in repr(config)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"token": "short"},
        {"token": TOKEN, "host": "0.0.0.0"},
        {"token": TOKEN, "port": -1},
        {"token": TOKEN, "port": True},
        {
            "token": TOKEN,
            "provider": {
                "baseUrl": "https://llm.example.invalid/v1",
                "model": "demo",
                "remoteOptIn": False,
            },
        },
        {
            "token": TOKEN,
            "provider": {
                "baseUrl": "http://llm.example.invalid/v1",
                "model": "demo",
                "remoteOptIn": True,
            },
        },
    ],
)
def test_sidecar_bootstrap_rejects_unsafe_payloads(payload: dict) -> None:
    with pytest.raises(ValueError):
        read_bootstrap(StringIO(json.dumps(payload) + "\n"))


def test_bootstrap_mode_requires_bearer_even_when_api_key_header_is_used() -> None:
    configure_bootstrap_token(TOKEN)
    try:
        with TestClient(create_app()) as client:
            assert client.get("/health").status_code == 401
            assert (
                client.get("/health", headers={"X-API-Key": TOKEN}).status_code == 401
            )
            response = client.get(
                "/health", headers={"Authorization": f"Bearer {TOKEN}"}
            )
            assert response.status_code == 200
            assert response.json()["protocol_version"] == "0.3.0"
    finally:
        configure_bootstrap_token(None)
