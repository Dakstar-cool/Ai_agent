from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.errors import LLMProviderBadResponseError, LLMProviderUnavailableError
from app.providers.llm.base import ILLMProvider
from app.providers.llm.models import LLMResponse, ProviderCapabilities, ToolCall

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(ILLMProvider):
    provider_name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.requires_network_permission = (
            urlsplit(self.base_url).hostname or ""
        ).casefold() not in {"127.0.0.1", "localhost", "::1"}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            trust_env=False,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.2),
        }
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions", json=payload
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            logger.warning(
                "LM Studio connect failed: base_url=%s model=%s",
                self.base_url,
                payload["model"],
            )
            raise LLMProviderUnavailableError(
                details={
                    "base_url": self.base_url,
                    "model": str(payload["model"]),
                    "reason": "connect_error",
                }
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "LM Studio timeout: base_url=%s model=%s timeout=%s",
                self.base_url,
                payload["model"],
                self.timeout,
            )
            raise LLMProviderUnavailableError(
                message="LLM backend timed out",
                details={
                    "base_url": self.base_url,
                    "model": str(payload["model"]),
                    "reason": "timeout",
                },
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            logger.warning(
                "LM Studio bad status: status=%s base_url=%s model=%s",
                status,
                self.base_url,
                payload["model"],
            )
            raise LLMProviderBadResponseError(
                message="LLM backend returned an HTTP error",
                details={
                    "base_url": self.base_url,
                    "model": str(payload["model"]),
                    "status_code": status,
                },
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "LM Studio request failed: base_url=%s model=%s error=%s",
                self.base_url,
                payload["model"],
                exc.__class__.__name__,
            )
            raise LLMProviderUnavailableError(
                details={
                    "base_url": self.base_url,
                    "model": str(payload["model"]),
                    "reason": exc.__class__.__name__,
                }
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning(
                "LM Studio returned non-JSON response: base_url=%s", self.base_url
            )
            raise LLMProviderBadResponseError(
                details={"base_url": self.base_url, "reason": "non_json_response"}
            ) from exc

        if not isinstance(data, dict):
            raise LLMProviderBadResponseError(
                message="LLM backend returned an invalid response object",
                details={"base_url": self.base_url},
            )

        choices = data.get("choices", [])
        if not isinstance(choices, list) or not choices:
            logger.warning(
                "LM Studio returned empty choices: base_url=%s", self.base_url
            )
            raise LLMProviderBadResponseError(
                message="LLM backend returned no choices",
                details={
                    "base_url": self.base_url,
                    "response_keys": sorted(data.keys()),
                },
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMProviderBadResponseError(
                message="LLM backend returned an invalid choice",
                details={"base_url": self.base_url},
            )

        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            raise LLMProviderBadResponseError(
                message="LLM backend returned an invalid message",
                details={"base_url": self.base_url},
            )

        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            logger.warning(
                "LM Studio returned non-string content: base_url=%s", self.base_url
            )
            raise LLMProviderBadResponseError(
                message="LLM backend returned invalid message content",
                details={
                    "base_url": self.base_url,
                    "content_type": type(content).__name__,
                },
            )

        tool_calls = self._parse_tool_calls(message.get("tool_calls", []))
        finish_reason = first_choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def discover_capabilities(self) -> ProviderCapabilities:
        try:
            response = await self._client.get(f"{self.base_url}/models")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMProviderUnavailableError(
                message="Provider capability discovery failed",
                details={
                    "provider": self.provider_name,
                    "model": self.model,
                    "reason": exc.__class__.__name__,
                },
            ) from exc

        entries = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            entries = []
        available_models = [
            str(entry["id"])[:200]
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        ][:500]
        selected = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("id") == self.model
            ),
            None,
        )
        context_limit = self._context_limit(selected)
        return ProviderCapabilities(
            provider=self.provider_name,
            model=self.model,
            tools=True,
            streaming=True,
            context_limit=context_limit,
            available_models=available_models,
        )

    @staticmethod
    def _context_limit(value: Any) -> int | None:
        if not isinstance(value, dict):
            return None
        for key in (
            "context_length",
            "max_context_length",
            "loaded_context_length",
        ):
            candidate = value.get(key)
            if isinstance(candidate, int) and candidate > 0:
                return candidate
        return None

    def _parse_tool_calls(self, raw_tool_calls: Any) -> list[ToolCall]:
        if raw_tool_calls is None:
            return []
        if not isinstance(raw_tool_calls, list):
            raise LLMProviderBadResponseError(
                message="LLM backend returned invalid tool calls",
                details={"base_url": self.base_url},
            )

        tool_calls: list[ToolCall] = []
        for raw_tool_call in raw_tool_calls:
            if not isinstance(raw_tool_call, dict):
                raise LLMProviderBadResponseError(
                    message="LLM backend returned an invalid tool call",
                    details={"base_url": self.base_url},
                )

            function = raw_tool_call.get("function")
            if not isinstance(function, dict):
                raise LLMProviderBadResponseError(
                    message="LLM backend returned an invalid tool function",
                    details={"base_url": self.base_url},
                )

            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise LLMProviderBadResponseError(
                        message="LLM backend returned invalid tool arguments",
                        details={"base_url": self.base_url},
                    ) from exc

            if not isinstance(arguments, dict):
                raise LLMProviderBadResponseError(
                    message="LLM tool arguments must be a JSON object",
                    details={"base_url": self.base_url},
                )

            try:
                tool_calls.append(
                    ToolCall(
                        id=raw_tool_call.get("id"),
                        name=function.get("name"),
                        arguments=arguments,
                        type=raw_tool_call.get("type", "function"),
                    )
                )
            except ValidationError as exc:
                raise LLMProviderBadResponseError(
                    message="LLM backend returned an incomplete tool call",
                    details={"base_url": self.base_url},
                ) from exc

        return tool_calls


class LMStudioProvider(OpenAICompatibleProvider):
    provider_name = "lmstudio"
