from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AppError(Exception):
    message: str
    code: str = "app_error"
    status_code: int = 500
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class LLMProviderUnavailableError(AppError):
    def __init__(self, message: str = "LLM backend is unavailable", details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="llm_backend_unavailable", status_code=503, details=details or {})


class LLMProviderBadResponseError(AppError):
    def __init__(self, message: str = "LLM backend returned an invalid response", details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="llm_backend_bad_response", status_code=502, details=details or {})
