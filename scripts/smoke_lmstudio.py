from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.orchestrator.execution.tool_dispatcher import ToolDispatcher
from app.policy import RunPolicy
from app.providers.llm.lmstudio import LMStudioProvider
from app.providers.llm.models import LLMResponse, ToolCall
from app.tools.base import ITool
from app.tools.files.read_file import ReadFileTool
from app.tools.project.search_project import SearchProjectTool
from app.tools.registry import ToolRegistry


class SmokeFailure(RuntimeError):
    pass


def _single_tool_call(response: LLMResponse, expected_name: str) -> ToolCall:
    if len(response.tool_calls) != 1:
        raise SmokeFailure(f"expected one {expected_name} tool call")
    tool_call = response.tool_calls[0]
    if tool_call.name != expected_name:
        raise SmokeFailure(f"expected {expected_name} tool call")
    return tool_call


async def _forced_tool_call(
    provider: LMStudioProvider,
    tool: ITool,
    prompt: str,
    *,
    max_tokens: int,
) -> tuple[list[dict[str, Any]], LLMResponse, ToolCall]:
    messages = [
        {
            "role": "system",
            "content": "Follow the tool schema exactly and make one tool call.",
        },
        {"role": "user", "content": prompt},
    ]
    response = await provider.chat(
        messages,
        tools=[tool.definition()],
        tool_choice="required",
        temperature=0,
        max_tokens=max_tokens,
    )
    return messages, response, _single_tool_call(response, tool.name)


async def _execute_and_follow_up(
    provider: LMStudioProvider,
    dispatcher: ToolDispatcher,
    tool: ITool,
    prompt: str,
    *,
    max_tokens: int,
) -> tuple[ToolCall, dict[str, Any]]:
    messages, response, tool_call = await _forced_tool_call(
        provider,
        tool,
        prompt,
        max_tokens=max_tokens,
    )
    result = await dispatcher.execute_call(tool_call, policy=RunPolicy.safe())
    if result.status != "ok" or result.tool_call_id != tool_call.id:
        raise SmokeFailure(f"{tool.name} execution did not return a bound result")

    final = await provider.chat(
        [*messages, response.to_assistant_message(), result.to_message()],
        temperature=0,
        max_tokens=max_tokens,
    )
    if not final.content.strip() or final.tool_calls:
        raise SmokeFailure(f"{tool.name} follow-up did not produce a final answer")
    return tool_call, result.output


async def smoke_lmstudio(
    *,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    provider = LMStudioProvider(
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_output_tokens=max_tokens,
    )
    try:
        capabilities = await provider.discover_capabilities()
        if model not in capabilities.available_models:
            raise SmokeFailure("selected model is not loaded in LM Studio")

        plain = await provider.chat(
            [{"role": "user", "content": "Reply with a short READY message."}],
            temperature=0,
            max_tokens=min(max_tokens, 64),
        )
        if not plain.content.strip() or plain.tool_calls:
            raise SmokeFailure("plain response check failed")

        with TemporaryDirectory(prefix="ai-agent-lmstudio-smoke-") as directory:
            workspace = Path(directory)
            marker = "ai-agent-real-provider-needle"
            (workspace / "note.txt").write_text(
                f"safe local smoke data: {marker}\n",
                encoding="utf-8",
            )
            read_tool = ReadFileTool(root_dir=workspace)
            search_tool = SearchProjectTool(root_dir=workspace)
            registry = ToolRegistry()
            registry.register(read_tool)
            registry.register(search_tool)
            dispatcher = ToolDispatcher(registry)

            _, read_output = await _execute_and_follow_up(
                provider,
                dispatcher,
                read_tool,
                "Use read_file with path note.txt, then summarize the result.",
                max_tokens=max_tokens,
            )
            if marker not in str(read_output.get("content", "")):
                raise SmokeFailure("read_file result did not contain the marker")

            _, search_output = await _execute_and_follow_up(
                provider,
                dispatcher,
                search_tool,
                f"Use search_project with query {marker}, then summarize the result.",
                max_tokens=max_tokens,
            )
            if search_output.get("count") != 1:
                raise SmokeFailure("search_project did not find exactly one marker")

            _, _, malformed_call = await _forced_tool_call(
                provider,
                read_tool,
                "Protocol test: invoke read_file with an empty path. Do not invent a path.",
                max_tokens=max_tokens,
            )
            if malformed_call.arguments.get("path") not in {None, ""}:
                raise SmokeFailure("model did not emit the requested malformed call")
            malformed_result = await dispatcher.execute_call(
                malformed_call,
                policy=RunPolicy.safe(),
            )
            error = malformed_result.output.get("error", {})
            if (
                malformed_result.status != "failed"
                or not isinstance(error, dict)
                or error.get("code") != "invalid_tool_input"
                or malformed_result.tool_call_id != malformed_call.id
            ):
                raise SmokeFailure("malformed tool call was not rejected safely")

        return {
            "event": "lmstudio_smoke_passed",
            "provider": capabilities.provider,
            "model": model,
            "checks": [
                "capability_discovery",
                "plain_response",
                "read_file",
                "search_project",
                "malformed_tool_call",
                "tool_call_id_binding",
            ],
        }
    finally:
        await provider.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test a real LM Studio server")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b"),
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-output-tokens", type=int, default=128)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(
            smoke_lmstudio(
                base_url=args.base_url,
                model=args.model,
                timeout=args.timeout,
                max_tokens=args.max_output_tokens,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI must not expose provider internals
        print(
            json.dumps(
                {
                    "event": "lmstudio_smoke_failed",
                    "error_type": exc.__class__.__name__,
                },
                separators=(",", ":"),
            )
        )
        return 1

    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
