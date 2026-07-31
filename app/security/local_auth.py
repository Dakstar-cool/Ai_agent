from __future__ import annotations

from threading import Lock

_lock = Lock()
_bootstrap_token: str | None = None


def configure_bootstrap_token(token: str | None) -> None:
    if token is not None and (not isinstance(token, str) or len(token) < 43):
        raise ValueError("Bootstrap token must contain at least 256 bits")
    global _bootstrap_token
    with _lock:
        _bootstrap_token = token


def get_bootstrap_token() -> str | None:
    with _lock:
        return _bootstrap_token
