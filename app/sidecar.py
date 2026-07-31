from __future__ import annotations

import base64
import json
import socket
import sys
from dataclasses import dataclass, field
from typing import TextIO

import uvicorn

from app.contracts import PROTOCOL_VERSION
from app.providers.llm.runtime_config import (
    ProviderRuntimeConfig,
    configure_runtime_provider,
)
from app.security import configure_bootstrap_token


MAX_BOOTSTRAP_BYTES = 8_192


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    token: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 0
    provider: ProviderRuntimeConfig | None = None


def read_bootstrap(stream: TextIO) -> BootstrapConfig:
    line = stream.readline(MAX_BOOTSTRAP_BYTES + 1)
    if not line or len(line.encode("utf-8")) > MAX_BOOTSTRAP_BYTES:
        raise ValueError("Invalid sidecar bootstrap payload")
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Invalid sidecar bootstrap payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid sidecar bootstrap payload")

    token = payload.get("token")
    host = payload.get("host", "127.0.0.1")
    port = payload.get("port", 0)
    provider = _parse_provider(payload.get("provider"))
    if not isinstance(token, str) or not _is_256_bit_token(token):
        raise ValueError("Invalid sidecar bootstrap token")
    if host != "127.0.0.1":
        raise ValueError("Sidecar host must be IPv4 loopback")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65_535:
        raise ValueError("Invalid sidecar bootstrap port")
    return BootstrapConfig(token=token, host=host, port=port, provider=provider)


def _parse_provider(value: object) -> ProviderRuntimeConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid provider bootstrap payload")
    base_url = value.get("baseUrl")
    model = value.get("model")
    api_key = value.get("apiKey")
    remote_opt_in = value.get("remoteOptIn", False)
    if not isinstance(base_url, str) or not isinstance(model, str):
        raise ValueError("Invalid provider bootstrap payload")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError("Invalid provider bootstrap payload")
    if not isinstance(remote_opt_in, bool):
        raise ValueError("Invalid provider bootstrap payload")
    return ProviderRuntimeConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        remote_opt_in=remote_opt_in,
    ).validate()


def _is_256_bit_token(token: str) -> bool:
    if len(token) != 43:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token + "=")
    except (ValueError, TypeError):
        return False
    return len(decoded) == 32


def create_loopback_socket(config: BootstrapConfig) -> socket.socket:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((config.host, config.port))
    server_socket.listen(128)
    return server_socket


def main() -> int:
    try:
        bootstrap = read_bootstrap(sys.stdin)
        configure_bootstrap_token(bootstrap.token)
        configure_runtime_provider(bootstrap.provider)
        server_socket = create_loopback_socket(bootstrap)
    except (OSError, ValueError):
        print("AI Agent worker bootstrap failed", file=sys.stderr, flush=True)
        return 2

    from app.main import app

    port = int(server_socket.getsockname()[1])
    print(
        json.dumps(
            {
                "event": "ready",
                "host": bootstrap.host,
                "port": port,
                "protocol_version": PROTOCOL_VERSION,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=bootstrap.host,
            port=port,
            log_config=None,
            access_log=False,
        )
    )
    server.run(sockets=[server_socket])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
