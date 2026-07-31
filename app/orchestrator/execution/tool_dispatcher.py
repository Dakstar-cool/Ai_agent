from __future__ import annotations

import logging
import json
from hashlib import sha256
from typing import Any

from app.errors import AppError
from app.policy import PolicyAction, PolicyEngine, PolicyUsage, RunPolicy
from app.providers.llm.models import ToolCall
from app.tools.models import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self.registry = registry
        self.policy_engine = policy_engine or PolicyEngine()

    async def execute(
        self,
        step: dict[str, Any],
        *,
        policy: RunPolicy | None = None,
        policy_usage: PolicyUsage | None = None,
    ) -> dict[str, Any]:
        tool_call = ToolCall(
            id=str(step.get("tool_call_id") or "legacy-tool-call"),
            name=step["tool_name"],
            arguments=step.get("args", {}),
        )
        result = await self.execute_call(
            tool_call,
            policy=policy,
            policy_usage=policy_usage,
        )
        return {
            "tool": result.name,
            "result": result.output,
            "status": result.status,
            "tool_call_id": result.tool_call_id,
            "audit": result.audit,
        }

    async def execute_call(
        self,
        tool_call: ToolCall,
        *,
        approved_mutation: bool = False,
        mutation_preview: dict[str, Any] | None = None,
        policy: RunPolicy | None = None,
        policy_usage: PolicyUsage | None = None,
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

        active_policy = policy or RunPolicy.safe()
        usage = policy_usage or PolicyUsage()
        decision = self.policy_engine.evaluate(
            tool=tool,
            arguments=tool_call.arguments,
            policy=active_policy,
            usage=usage,
            explicit_approval=approved_mutation,
        )
        policy_payload = decision.model_dump(mode="json")
        if decision.action is PolicyAction.DENY:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                output={
                    "error": {
                        "code": "policy_denied",
                        "message": "Tool execution is blocked by policy",
                    },
                    "policy": policy_payload,
                },
            )

        generated_preview = mutation_preview
        if not tool.read_only and generated_preview is None:
            preview = getattr(tool, "preview", None)
            if preview is not None:
                try:
                    generated_preview = await preview(**tool_call.arguments)
                except AppError as exc:
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        status="failed",
                        output={
                            "error": {
                                "code": exc.code,
                                "message": "Tool rejected the mutation preview",
                            }
                        },
                    )

        if decision.action is PolicyAction.APPROVAL_REQUIRED:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="approval_required",
                output={
                    "error": {
                        "code": "approval_required",
                        "message": "This tool can mutate state and requires explicit approval",
                    },
                    **(
                        {"mutation_preview": generated_preview}
                        if generated_preview is not None
                        else {}
                    ),
                    "policy": policy_payload,
                },
            )

        try:
            apply_preview = getattr(tool, "apply_preview", None)
            if apply_preview:
                if generated_preview is None:
                    raise AppError(
                        message="Mutation preview is required for this tool",
                        code="mutation_preview_required",
                        status_code=409,
                    )
                output = await apply_preview(
                    mutation_preview=generated_preview, **tool_call.arguments
                )
            else:
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

        audit: dict[str, Any] | None = None
        if not tool.read_only:
            usage.record(tool.mutation_kind or "mutation")
            output_hash = output.get("new_sha256")
            post_action_hash = (
                output_hash
                if isinstance(output_hash, str) and len(output_hash) == 64
                else sha256(
                    json.dumps(
                        output,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
            )
            audit = {
                "schema_version": "0.1.0",
                "tool_call_id": tool_call.id,
                "tool": tool.name,
                "mutation_kind": tool.mutation_kind or "mutation",
                "decision": policy_payload,
                "post_action_hash": post_action_hash,
            }

        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="ok",
            output={**output, "policy": policy_payload},
            audit=audit,
        )
