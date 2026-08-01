from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from app.errors import AppError
from app.orchestrator.approval.store import PendingApproval, PendingApprovalStore
from app.orchestrator.context.builder import ContextBuilder
from app.orchestrator.execution.tool_dispatcher import ToolDispatcher
from app.orchestrator.planning.planner import Planner
from app.orchestrator.routing.router import TaskRouter
from app.orchestrator.session.manager import SessionManager
from app.orchestrator.synthesis.result_synthesizer import ResultSynthesizer
from app.orchestrator.verification.code_verifier import CodeVerifier
from app.orchestrator.verification.verifier import Verifier
from app.policy import PolicyUsage, RunPolicy
from app.providers.llm.base import ILLMProvider
from app.providers.llm.models import LLMResponse, ToolCall
from app.providers.memory.base import IMemoryService
from app.providers.memory.policy import contains_sensitive_data
from app.schemas.chat import ChatRequest, ChatResponse, ExecutionStep
from app.tools.models import ToolResult
from app.tools.registry import ToolRegistry
from app.utils.request_context import get_request_id

logger = logging.getLogger(__name__)


class _ObservedExecutionLog(list[ExecutionStep]):
    def __init__(self, observer: Callable[[ExecutionStep], None] | None = None) -> None:
        super().__init__()
        self._observer = observer

    def append(self, step: ExecutionStep) -> None:
        super().append(step)
        if self._observer is not None:
            self._observer(step)


class Orchestrator:
    def __init__(
        self,
        llm_provider: ILLMProvider,
        memory_service: IMemoryService,
        tool_registry: ToolRegistry,
        *,
        session_manager: SessionManager | None = None,
        router: TaskRouter | None = None,
        context_builder: ContextBuilder | None = None,
        planner: Planner | None = None,
        dispatcher: ToolDispatcher | None = None,
        verifier: Verifier | None = None,
        code_verifier: CodeVerifier | None = None,
        synthesizer: ResultSynthesizer | None = None,
        max_steps: int = 6,
        max_tool_calls: int = 10,
        agent_timeout_seconds: float = 120.0,
        approval_store: PendingApprovalStore | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.memory_service = memory_service
        self.tool_registry = tool_registry

        self.session_manager = session_manager or SessionManager()
        self.router = router or TaskRouter()
        self.context_builder = context_builder or ContextBuilder(memory_service)
        self.planner = planner or Planner()
        self.dispatcher = dispatcher or ToolDispatcher(tool_registry)
        self.verifier = verifier or Verifier()
        self.code_verifier = code_verifier
        self.synthesizer = synthesizer or ResultSynthesizer()
        self.max_steps = max(1, max_steps)
        self.max_tool_calls = max(1, max_tool_calls)
        self.agent_timeout_seconds = max(0.1, agent_timeout_seconds)
        self.approval_store = approval_store or PendingApprovalStore()

    async def handle(
        self,
        request: ChatRequest,
        *,
        on_step: Callable[[ExecutionStep], None] | None = None,
    ) -> ChatResponse:
        request_id = get_request_id()
        session = self.session_manager.get_or_create(request.session_id)
        try:
            run_policy = RunPolicy.model_validate(
                request.metadata.get("run_policy", RunPolicy.safe())
            )
        except ValueError as exc:
            raise AppError(
                message="Run policy is invalid",
                code="invalid_run_policy",
                status_code=400,
            ) from exc
        policy_usage = PolicyUsage()
        if (
            getattr(self.llm_provider, "requires_network_permission", False)
            and not run_policy.network_allowed
        ):
            raise AppError(
                message="Remote LLM provider requires explicit run network permission",
                code="network_permission_required",
                status_code=403,
            )

        execution_log: list[ExecutionStep] = _ObservedExecutionLog(on_step)
        approved_exchange = await self._maybe_execute_approved_tool(
            request=request,
            session_id=session.session_id,
            execution_log=execution_log,
            run_policy=run_policy,
            policy_usage=policy_usage,
        )

        route = (
            approved_exchange[0].route
            if approved_exchange is not None
            else self.router.route(request.message)
        )
        project_path = (
            approved_exchange[0].project_path
            if approved_exchange is not None
            else request.project_path
        )
        context = await self.context_builder.build(
            session=session,
            message=request.message,
            route=route,
            project_path=project_path,
            project_id=self._metadata_identifier(request.metadata, "workspace_id"),
            user_id=self._metadata_identifier(request.metadata, "user_id"),
        )
        plan = self.planner.make_plan(context=context, route=route)
        if approved_exchange is not None:
            self._attach_approved_exchange(plan, *approved_exchange)

        logger.info(
            "orchestrator_handle_start request_id=%s session_id=%s route=%s message_len=%s plan_steps=%s",
            request_id,
            session.session_id,
            route,
            len(request.message),
            len(plan),
        )

        llm_reply = ""

        for index, step in enumerate(plan, start=1):
            llm_reply = await self._execute_step(
                step=step,
                index=index,
                session_id=session.session_id,
                route=route,
                project_path=project_path,
                current_reply=llm_reply,
                execution_log=execution_log,
                run_policy=run_policy,
                policy_usage=policy_usage,
            )

        ok, error = self.verifier.verify(llm_reply)
        if not ok:
            logger.warning(
                "verification_failed request_id=%s session_id=%s error=%s",
                request_id,
                session.session_id,
                error,
            )
            execution_log.append(
                ExecutionStep(
                    name="verification",
                    status="failed",
                    payload={"error": error or "unknown"},
                )
            )
            llm_reply = f"Execution failed verification: {error}"
        else:
            execution_log.append(
                ExecutionStep(name="verification", status="ok", payload={})
            )

        code_verification_ok = await self._maybe_run_code_verifier(
            request=request,
            route=route,
            session_id=session.session_id,
            execution_log=execution_log,
        )
        overall_verification_ok = ok and code_verification_ok
        if ok and not code_verification_ok:
            llm_reply = "Execution failed code verification."

        await self._save_memory(
            request=request,
            session_id=session.session_id,
            route=route,
            llm_reply=llm_reply,
            verification_ok=overall_verification_ok,
        )

        # Важно: сохраняем user message только после сборки контекста.
        # Иначе текущее сообщение попадает в историю дважды:
        # один раз через session.history и второй раз через context['user_message'].
        self.session_manager.append_message(session.session_id, "user", request.message)
        self.session_manager.append_message(session.session_id, "assistant", llm_reply)

        response_payload = self.synthesizer.synthesize(
            llm_reply=llm_reply,
            execution_log=[step.model_dump() for step in execution_log],
        )

        logger.info(
            "orchestrator_handle_done request_id=%s session_id=%s route=%s execution_steps=%s",
            request_id,
            session.session_id,
            route,
            len(execution_log),
        )
        return ChatResponse(
            session_id=session.session_id,
            route=route,
            reply=response_payload["reply"],
            steps=execution_log,
        )

    async def _execute_step(
        self,
        *,
        step: dict[str, Any],
        index: int,
        session_id: str,
        route: str,
        project_path: str | None,
        current_reply: str,
        execution_log: list[ExecutionStep],
        run_policy: RunPolicy,
        policy_usage: PolicyUsage,
    ) -> str:
        kind = step.get("kind")
        logger.info(
            "execution_step_start session_id=%s index=%s kind=%s",
            session_id,
            index,
            kind,
        )

        if kind == "tool":
            await self._execute_tool_step(
                step=step,
                index=index,
                session_id=session_id,
                execution_log=execution_log,
                run_policy=run_policy,
                policy_usage=policy_usage,
            )
            return current_reply

        if kind == "llm":
            return await self._execute_llm_step(
                step=step,
                index=index,
                session_id=session_id,
                route=route,
                project_path=project_path,
                execution_log=execution_log,
                run_policy=run_policy,
                policy_usage=policy_usage,
            )

        execution_log.append(
            ExecutionStep(
                name=f"step_{index}",
                status="failed",
                payload={"reason": "unsupported_step_kind", "kind": kind},
            )
        )
        raise AppError(
            message="Planner returned an unsupported execution step",
            code="unsupported_execution_step",
            status_code=500,
            details={"index": index, "kind": kind},
        )

    async def _execute_tool_step(
        self,
        *,
        step: dict[str, Any],
        index: int,
        session_id: str,
        execution_log: list[ExecutionStep],
        run_policy: RunPolicy,
        policy_usage: PolicyUsage,
    ) -> None:
        tool_name = step.get("tool_name")
        if not tool_name:
            execution_log.append(
                ExecutionStep(
                    name=f"step_{index}",
                    status="failed",
                    payload={"reason": "missing_tool_name"},
                )
            )
            raise AppError(
                message="Tool step is missing tool_name",
                code="invalid_tool_step",
                status_code=500,
                details={"index": index},
            )

        try:
            tool_result = await self.dispatcher.execute(
                step,
                policy=run_policy,
                policy_usage=policy_usage,
            )

            result_payload = tool_result["result"]
            status = tool_result.get("status", "ok")

            if status == "ok" and isinstance(result_payload, dict):
                exit_code = result_payload.get(
                    "exit_code", result_payload.get("returncode")
                )
                if exit_code not in (None, 0):
                    status = "failed"

            execution_log.append(
                ExecutionStep(
                    name=tool_result["tool"],
                    status=status,
                    payload=result_payload,
                )
            )
            audit = tool_result.get("audit")
            if isinstance(audit, dict):
                self._append_policy_audit(execution_log, audit)

            logger.info(
                "execution_step_done session_id=%s index=%s kind=tool tool=%s status=%s",
                session_id,
                index,
                tool_result["tool"],
                status,
            )

        except KeyError as exc:
            logger.warning(
                "execution_step_failed session_id=%s index=%s kind=tool tool=%s reason=tool_not_found",
                session_id,
                index,
                tool_name,
            )
            execution_log.append(
                ExecutionStep(
                    name=tool_name,
                    status="failed",
                    payload={"error": "tool_not_found"},
                )
            )
            raise AppError(
                message=f"Tool is not registered: {tool_name}",
                code="tool_not_found",
                status_code=400,
                details={"tool_name": tool_name},
            ) from exc

        except AppError:
            execution_log.append(
                ExecutionStep(
                    name=tool_name,
                    status="failed",
                    payload={"error": "tool_execution_failed"},
                )
            )
            raise

        except Exception as exc:
            logger.exception(
                "execution_step_failed session_id=%s index=%s kind=tool tool=%s error=%s",
                session_id,
                index,
                tool_name,
                exc.__class__.__name__,
            )
            execution_log.append(
                ExecutionStep(
                    name=tool_name,
                    status="failed",
                    payload={"error": exc.__class__.__name__},
                )
            )
            raise AppError(
                message=f"Tool execution failed: {tool_name}",
                code="tool_execution_failed",
                status_code=500,
                details={"tool_name": tool_name, "error_type": exc.__class__.__name__},
            ) from exc

    async def _execute_llm_step(
        self,
        *,
        step: dict[str, Any],
        index: int,
        session_id: str,
        route: str,
        project_path: str | None,
        execution_log: list[ExecutionStep],
        run_policy: RunPolicy,
        policy_usage: PolicyUsage,
    ) -> str:
        args = step.get("args")
        if not isinstance(args, dict):
            execution_log.append(
                ExecutionStep(
                    name="llm_chat",
                    status="failed",
                    payload={"reason": "missing_llm_args"},
                )
            )
            raise AppError(
                message="LLM step is missing args",
                code="invalid_llm_step",
                status_code=500,
                details={"index": index},
            )

        try:
            messages = args.get("messages")
            if not isinstance(messages, list):
                raise AppError(
                    message="LLM step messages must be a list",
                    code="invalid_llm_step",
                    status_code=500,
                    details={"index": index},
                )
            initial_tool_calls = args.get("initial_tool_calls", [])
            if not isinstance(initial_tool_calls, list) or not all(
                isinstance(tool_call, ToolCall) for tool_call in initial_tool_calls
            ):
                raise AppError(
                    message="Initial tool calls must be typed ToolCall values",
                    code="invalid_llm_step",
                    status_code=500,
                    details={"index": index},
                )
            llm_reply = await self._run_agent_loop(
                messages=messages,
                session_id=session_id,
                route=route,
                project_path=project_path,
                initial_tool_calls=initial_tool_calls,
                execution_log=execution_log,
                run_policy=run_policy,
                policy_usage=policy_usage,
            )
        except AppError as exc:
            execution_log.append(
                ExecutionStep(
                    name="llm_chat",
                    status="failed",
                    payload={"error": exc.code},
                )
            )
            logger.warning(
                "execution_step_failed session_id=%s index=%s kind=llm code=%s",
                session_id,
                index,
                exc.code,
            )
            raise
        except Exception as exc:
            execution_log.append(
                ExecutionStep(
                    name="llm_chat",
                    status="failed",
                    payload={"error": exc.__class__.__name__},
                )
            )
            logger.exception(
                "execution_step_failed session_id=%s index=%s kind=llm error=%s",
                session_id,
                index,
                exc.__class__.__name__,
            )
            raise AppError(
                message="LLM execution failed",
                code="llm_execution_failed",
                status_code=500,
                details={"error_type": exc.__class__.__name__},
            ) from exc

        logger.info(
            "execution_step_done session_id=%s index=%s kind=llm reply_len=%s",
            session_id,
            index,
            len(llm_reply),
        )
        return llm_reply

    async def _maybe_execute_approved_tool(
        self,
        *,
        request: ChatRequest,
        session_id: str,
        execution_log: list[ExecutionStep],
        run_policy: RunPolicy,
        policy_usage: PolicyUsage,
    ) -> tuple[PendingApproval, ToolResult] | None:
        raw_approval_id = request.metadata.get("approve_tool_call_id")
        if raw_approval_id is None:
            return None
        if not isinstance(raw_approval_id, str) or not raw_approval_id.strip():
            raise AppError(
                message="approve_tool_call_id must be a non-empty string",
                code="invalid_approval_request",
                status_code=400,
            )

        approval_id = raw_approval_id.strip()
        pending = self.approval_store.consume(
            approval_id=approval_id,
            session_id=session_id,
        )
        tool_result = await self.dispatcher.execute_call(
            pending.tool_call,
            approved_mutation=True,
            mutation_preview=pending.mutation_preview,
            policy=run_policy,
            policy_usage=policy_usage,
        )
        tool_result = tool_result.model_copy(
            update={
                "output": {
                    **tool_result.output,
                    "approval_id": approval_id,
                    "approved": True,
                }
            }
        )
        execution_log.append(
            ExecutionStep(
                name=tool_result.name,
                status=tool_result.status,
                payload={
                    "tool_call_id": tool_result.tool_call_id,
                    **tool_result.output,
                },
            )
        )
        if tool_result.audit is not None:
            self._append_policy_audit(execution_log, tool_result.audit)
        logger.info(
            "approved_tool_executed session_id=%s tool=%s status=%s",
            session_id,
            pending.tool_call.name,
            tool_result.status,
        )
        return pending, tool_result

    def _attach_approved_exchange(
        self,
        plan: list[dict[str, Any]],
        pending: PendingApproval,
        tool_result: ToolResult,
    ) -> None:
        llm_step = next((step for step in plan if step.get("kind") == "llm"), None)
        if llm_step is None:
            raise AppError(
                message="Planner did not return an LLM step for approved tool result",
                code="invalid_approval_plan",
                status_code=500,
            )
        args = llm_step.get("args")
        messages = args.get("messages") if isinstance(args, dict) else None
        if not isinstance(messages, list):
            raise AppError(
                message="Planner returned invalid messages for approved tool result",
                code="invalid_approval_plan",
                status_code=500,
            )

        messages.append(
            LLMResponse(
                tool_calls=[pending.tool_call], finish_reason="tool_calls"
            ).to_assistant_message()
        )
        messages.append(tool_result.to_message())
        args["initial_tool_calls"] = [pending.tool_call]

    def _register_pending_approval(
        self,
        *,
        session_id: str,
        route: str,
        project_path: str | None,
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> ToolResult:
        mutation_preview = tool_result.output.get("mutation_preview")
        pending = self.approval_store.create(
            session_id=session_id,
            tool_call=tool_call,
            route=route,
            project_path=project_path,
            mutation_preview=(
                mutation_preview if isinstance(mutation_preview, dict) else None
            ),
        )
        output = dict(tool_result.output)
        output.pop("mutation_preview", None)
        if pending.mutation_preview is not None:
            output["mutation_preview"] = pending.mutation_preview
        return tool_result.model_copy(
            update={
                "output": {
                    **output,
                    "approval_id": pending.approval_id,
                    "preview_hash": pending.approval_hash,
                    "expires_in_seconds": round(
                        self.approval_store.remaining_seconds(pending), 3
                    ),
                }
            }
        )

    async def _run_agent_loop(
        self,
        *,
        messages: list[dict[str, Any]],
        session_id: str,
        route: str,
        project_path: str | None,
        initial_tool_calls: list[ToolCall],
        execution_log: list[ExecutionStep],
        run_policy: RunPolicy,
        policy_usage: PolicyUsage,
    ) -> str:
        conversation = [dict(message) for message in messages]
        tool_definitions = self.tool_registry.definitions()
        seen_calls = {
            self._tool_call_signature(tool_call) for tool_call in initial_tool_calls
        }
        tool_call_count = len(initial_tool_calls)

        try:
            async with asyncio.timeout(self.agent_timeout_seconds):
                for loop_step in range(1, self.max_steps + 1):
                    chat_args: dict[str, Any] = {"messages": conversation}
                    if tool_definitions:
                        chat_args.update(
                            {"tools": tool_definitions, "tool_choice": "auto"}
                        )

                    raw_response = await self.llm_provider.chat(**chat_args)
                    response = self._normalize_llm_response(raw_response)
                    execution_log.append(
                        ExecutionStep(
                            name="llm_chat",
                            status="ok",
                            payload={
                                "loop_step": loop_step,
                                "finish_reason": response.finish_reason,
                                "tool_call_count": len(response.tool_calls),
                                "reply_preview": response.content[:200],
                            },
                        )
                    )

                    if not response.tool_calls:
                        return response.content

                    if tool_call_count + len(response.tool_calls) > self.max_tool_calls:
                        self._append_loop_stop(
                            execution_log,
                            reason="max_tool_calls_exceeded",
                            loop_step=loop_step,
                            tool_call_count=tool_call_count,
                        )
                        return (
                            "Agent execution stopped: maximum tool call limit reached."
                        )

                    conversation.append(response.to_assistant_message())
                    for tool_call in response.tool_calls:
                        execution_log.append(
                            ExecutionStep(
                                name="tool_call",
                                status="requested",
                                payload=tool_call.model_dump(mode="json"),
                            )
                        )
                        signature = self._tool_call_signature(tool_call)
                        if signature in seen_calls:
                            tool_result = self._duplicate_tool_result(tool_call)
                        else:
                            seen_calls.add(signature)
                            tool_result = await self.dispatcher.execute_call(
                                tool_call,
                                policy=run_policy,
                                policy_usage=policy_usage,
                            )
                            if tool_result.status == "approval_required":
                                tool_result = self._register_pending_approval(
                                    session_id=session_id,
                                    route=route,
                                    project_path=project_path,
                                    tool_call=tool_call,
                                    tool_result=tool_result,
                                )

                        tool_call_count += 1
                        execution_log.append(
                            ExecutionStep(
                                name=tool_result.name,
                                status=tool_result.status,
                                payload={
                                    "tool_call_id": tool_result.tool_call_id,
                                    **tool_result.output,
                                },
                            )
                        )
                        if tool_result.audit is not None:
                            self._append_policy_audit(execution_log, tool_result.audit)
                        conversation.append(tool_result.to_message())

                self._append_loop_stop(
                    execution_log,
                    reason="max_steps_exceeded",
                    loop_step=self.max_steps,
                    tool_call_count=tool_call_count,
                )
                return "Agent execution stopped: maximum step limit reached."
        except TimeoutError:
            self._append_loop_stop(
                execution_log,
                reason="deadline_exceeded",
                loop_step=None,
                tool_call_count=tool_call_count,
            )
            logger.warning(
                "agent_loop_timeout session_id=%s timeout_seconds=%s",
                session_id,
                self.agent_timeout_seconds,
            )
            return "Agent execution stopped: execution deadline reached."

    @staticmethod
    def _append_policy_audit(
        execution_log: list[ExecutionStep], audit: dict[str, Any]
    ) -> None:
        execution_log.append(
            ExecutionStep(name="policy_audit", status="ok", payload=audit)
        )

    def _normalize_llm_response(self, response: Any) -> LLMResponse:
        if isinstance(response, LLMResponse):
            return response
        if isinstance(response, str):
            return LLMResponse(content=response, finish_reason="stop")
        raise AppError(
            message="LLM provider returned an unsupported response type",
            code="llm_backend_bad_response",
            status_code=502,
            details={"response_type": response.__class__.__name__},
        )

    def _tool_call_signature(self, tool_call: ToolCall) -> str:
        arguments = json.dumps(
            tool_call.arguments, ensure_ascii=False, sort_keys=True, default=str
        )
        return f"{tool_call.name}:{arguments}"

    def _duplicate_tool_result(self, tool_call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="failed",
            output={
                "error": {
                    "code": "duplicate_tool_call",
                    "message": "An identical tool call was already processed",
                }
            },
        )

    def _append_loop_stop(
        self,
        execution_log: list[ExecutionStep],
        *,
        reason: str,
        loop_step: int | None,
        tool_call_count: int,
    ) -> None:
        execution_log.append(
            ExecutionStep(
                name="agent_loop",
                status="failed",
                payload={
                    "reason": reason,
                    "loop_step": loop_step,
                    "tool_call_count": tool_call_count,
                },
            )
        )

    async def _save_memory(
        self,
        *,
        request: ChatRequest,
        session_id: str,
        route: str,
        llm_reply: str,
        verification_ok: bool,
    ) -> None:
        if not self._should_save_memory(
            request=request, llm_reply=llm_reply, verification_ok=verification_ok
        ):
            logger.info("memory_save_skipped session_id=%s route=%s", session_id, route)
            return

        try:
            from app.providers.memory.models import MemoryRecord

            await self.memory_service.save(
                MemoryRecord(
                    kind="interaction_summary",
                    session_id=session_id,
                    user_id=self._metadata_identifier(request.metadata, "user_id"),
                    project_id=self._metadata_identifier(
                        request.metadata, "workspace_id"
                    ),
                    summary=(
                        f"Request: {request.message.strip()[:1200]}\n"
                        f"Decision: {llm_reply.strip()[:2400]}"
                    ),
                    route=route,
                    provenance={
                        "source": "orchestrator",
                        "route": route,
                    },
                    project_path=request.project_path,
                )
            )
        except Exception as exc:  # noqa: BLE001 - memory backend isolation boundary
            logger.warning(
                "memory_save_failed session_id=%s error=%s",
                session_id,
                exc.__class__.__name__,
            )

    def _should_save_memory(
        self, *, request: ChatRequest, llm_reply: str, verification_ok: bool
    ) -> bool:
        if not verification_ok:
            return False

        if not request.message.strip() or not llm_reply.strip():
            return False

        placeholders = {"string", "null", "none"}
        if request.message.strip().lower() in placeholders:
            return False

        if (
            request.project_path
            and request.project_path.strip().lower() in placeholders
        ):
            return False

        if llm_reply.startswith("Execution failed verification:"):
            return False

        if contains_sensitive_data(request.message):
            logger.info("memory_save_skipped reason=sensitive_data field=message")
            return False

        if contains_sensitive_data(request.metadata):
            logger.info("memory_save_skipped reason=sensitive_data field=metadata")
            return False

        if contains_sensitive_data(llm_reply):
            logger.info("memory_save_skipped reason=sensitive_data field=llm_reply")
            return False

        return True

    @staticmethod
    def _metadata_identifier(metadata: dict[str, Any], key: str) -> str | None:
        value = metadata.get(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value[:200] or None

    async def _maybe_run_code_verifier(
        self,
        *,
        request: ChatRequest,
        route: str,
        session_id: str,
        execution_log: list[ExecutionStep],
    ) -> bool:
        if route != "coding":
            return True
        if request.metadata.get("verify_code") is not True:
            return True
        if self.code_verifier is None:
            execution_log.append(
                ExecutionStep(
                    name="code_verifier",
                    status="failed",
                    payload={"error": "code_verifier_not_configured"},
                )
            )
            return False

        try:
            result = await self.code_verifier.verify()
        except Exception as exc:  # noqa: BLE001 - verifier isolation boundary
            logger.warning(
                "code_verifier_failed session_id=%s error=%s",
                session_id,
                exc.__class__.__name__,
            )
            execution_log.append(
                ExecutionStep(
                    name="code_verifier",
                    status="failed",
                    payload={
                        "error": "code_verifier_failed",
                        "error_type": exc.__class__.__name__,
                    },
                )
            )
            return False

        execution_log.append(
            ExecutionStep(
                name="code_verifier",
                status="ok" if result.get("ok") is True else "failed",
                payload=result,
            )
        )
        return result.get("ok") is True
