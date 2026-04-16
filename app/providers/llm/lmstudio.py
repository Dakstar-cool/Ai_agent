from __future__ import annotations

import logging
from typing import Any

import httpx

from app.errors import LLMProviderBadResponseError, LLMProviderUnavailableError
from app.providers.llm.base import ILLMProvider

logger = logging.getLogger(__name__)


class LMStudioProvider(ILLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        payload = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.2),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            logger.warning("LM Studio connect failed: base_url=%s model=%s", self.base_url, payload["model"])
            raise LLMProviderUnavailableError(
                details={"base_url": self.base_url, "model": str(payload["model"]), "reason": "connect_error"}
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning("LM Studio timeout: base_url=%s model=%s timeout=%s", self.base_url, payload["model"], self.timeout)
            raise LLMProviderUnavailableError(
                message="LLM backend timed out",
                details={"base_url": self.base_url, "model": str(payload["model"]), "reason": "timeout"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            body_preview = exc.response.text[:500] if exc.response is not None else ""
            logger.warning("LM Studio bad status: status=%s base_url=%s model=%s", status, self.base_url, payload["model"])
            raise LLMProviderBadResponseError(
                message="LLM backend returned an HTTP error",
                details={
                    "base_url": self.base_url,
                    "model": str(payload["model"]),
                    "status_code": status,
                    "body_preview": body_preview,
                },
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("LM Studio request failed: base_url=%s model=%s error=%s", self.base_url, payload["model"], exc.__class__.__name__)
            raise LLMProviderUnavailableError(
                details={"base_url": self.base_url, "model": str(payload["model"]), "reason": exc.__class__.__name__}
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("LM Studio returned non-JSON response: base_url=%s", self.base_url)
            raise LLMProviderBadResponseError(
                details={"base_url": self.base_url, "body_preview": response.text[:500]}
            ) from exc

        choices = data.get("choices", [])
        if not choices:
            logger.warning("LM Studio returned empty choices: base_url=%s", self.base_url)
            raise LLMProviderBadResponseError(
                message="LLM backend returned no choices",
                details={"base_url": self.base_url, "response_keys": sorted(data.keys())},
            )

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str):
            logger.warning("LM Studio returned non-string content: base_url=%s", self.base_url)
            raise LLMProviderBadResponseError(
                message="LLM backend returned invalid message content",
                details={"base_url": self.base_url, "content_type": type(content).__name__},
            )

        return content
