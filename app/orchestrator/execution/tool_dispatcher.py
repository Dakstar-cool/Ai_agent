from __future__ import annotations

import logging
from typing import Any

from app.errors import AppError
from app.providers.llm.models import ToolCall
from app.tools.models import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, step: dict[str, Any]) -> dict[str, Any]:
        tool_call = ToolCall(
            id=str(step.get("tool_call_id") or "legacy-tool-call"),
            name=step["tool_name"],
            arguments=step.get("args", {}),
        )
        result = await self.execute_call(tool_call)
        return {
            "tool": result.name,
            "result": result.output,
            "status": result.status,
            "tool_call_id": result.tool_call_id,
        }

    async def execute_call(
        self, tool_call: ToolCall, *, approved_mutation: bool = False
    ) -> ToolResult:
        try:
            tool = self.registry.get(tool_call.name)
        except KeyError:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                output={
                    "error": {
                        "code": "tool_not_found",
                        "message": "Requested tool is not available",
                    }
                },
            )

        if not tool.read_only and not approved_mutation:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="approval_required",
                output={
                    "error": {
                        "code": "approval_required",
                        "message": "This tool can mutate state and requires explicit approval",
                    }
                },
            )

        try:
            output = await tool.run(**tool_call.arguments)
        except AppError as exc:
            logger.warning(
                "tool_call_rejected tool=%s code=%s", tool_call.name, exc.code
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                output={
                    "error": {
                        "code": exc.code,
                        "message": "Tool rejected the request",
                    }
                },
            )
        except Exception as exc:
            logger.warning(
                "tool_call_failed tool=%s error_type=%s",
                tool_call.name,
                exc.__class__.__name__,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                output={
                    "error": {
                        "code": "tool_execution_failed",
                        "message": "Tool execution failed",
                    }
                },
            )

        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="ok",
            output=output,
        )
