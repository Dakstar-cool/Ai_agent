from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from urllib.parse import urlsplit


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class ProviderRuntimeConfig:
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    remote_opt_in: bool = False

    def validate(self) -> "ProviderRuntimeConfig":
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Provider URL must be HTTP or HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Provider URL contains unsupported credentials or metadata")
        is_loopback = parsed.hostname.casefold() in LOOPBACK_HOSTS
        if not is_loopback and (not self.remote_opt_in or parsed.scheme != "https"):
            raise ValueError("Remote provider requires explicit opt-in and HTTPS")
        if not self.model.strip() or len(self.model) > 200:
            raise ValueError("Provider model is invalid")
        if self.api_key is not None and len(self.api_key) > 4_096:
            raise ValueError("Provider API key is too long")
        return self

    @property
    def requires_network_permission(self) -> bool:
        hostname = urlsplit(self.base_url).hostname
        return hostname is not None and hostname.casefold() not in LOOPBACK_HOSTS


_lock = Lock()
_runtime_config: ProviderRuntimeConfig | None = None


def configure_runtime_provider(config: ProviderRuntimeConfig | None) -> None:
    if config is not None:
        config.validate()
    global _runtime_config
    with _lock:
        _runtime_config = config


def get_runtime_provider() -> ProviderRuntimeConfig | None:
    with _lock:
        return _runtime_config
