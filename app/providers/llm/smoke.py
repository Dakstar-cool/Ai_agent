from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.orchestrator.execution.tool_dispatcher import ToolDispatcher
from app.policy import RunPolicy
from app.providers.llm.base import ILLMProvider
from app.providers.llm.models import LLMResponse, ToolCall
from app.tools.base import ITool
from app.tools.files.read_file import ReadFileTool
from app.tools.project.search_project import SearchProjectTool
from app.tools.registry import ToolRegistry


class SmokeFailure(RuntimeError):
    """Safe failure raised by the real-provider diagnostic harness."""


def _single_tool_call(response: LLMResponse, expected_name: str) -> ToolCall:
    if len(response.tool_calls) != 1:
        raise SmokeFailure(f"expected one {expected_name} tool call")
    tool_call = response.tool_calls[0]
    if tool_call.name != expected_name:
        raise SmokeFailure(f"expected {expected_name} tool call")
    return tool_call


async def _forced_tool_call(
    provider: ILLMProvider,
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
    provider: ILLMProvider,
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


async def smoke_tool_provider(
    provider: ILLMProvider,
    *,
    max_tokens: int,
) -> dict[str, Any]:
    capabilities = await provider.discover_capabilities()
    if (
        capabilities.available_models
        and provider.model not in capabilities.available_models
    ):
        raise SmokeFailure("selected model is not available from the provider")
    if not capabilities.tools:
        raise SmokeFailure("selected model does not report tool support")

    plain = await provider.chat(
        [{"role": "user", "content": "Reply with a short READY message."}],
        temperature=0,
        max_tokens=min(max_tokens, 64),
    )
    if not plain.content.strip() or plain.tool_calls:
        raise SmokeFailure("plain response check failed")

    with TemporaryDirectory(prefix="ai-agent-provider-smoke-") as directory:
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

    event_provider = capabilities.provider.replace("-", "_")
    return {
        "event": f"{event_provider}_smoke_passed",
        "provider": capabilities.provider,
        "model": provider.model,
        "checks": [
            "capability_discovery",
            "plain_response",
            "read_file",
            "search_project",
            "malformed_tool_call",
            "tool_call_id_binding",
        ],
    }
