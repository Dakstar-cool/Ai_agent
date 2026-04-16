from __future__ import annotations

import logging
from typing import Any

from app.errors import AppError
from app.orchestrator.context.builder import ContextBuilder
from app.orchestrator.execution.tool_dispatcher import ToolDispatcher
from app.orchestrator.planning.planner import Planner
from app.orchestrator.routing.router import TaskRouter
from app.orchestrator.session.manager import SessionManager
from app.orchestrator.synthesis.result_synthesizer import ResultSynthesizer
from app.orchestrator.verification.verifier import Verifier
from app.providers.llm.base import ILLMProvider
from app.providers.memory.base import IMemoryService
from app.schemas.chat import ChatRequest, ChatResponse, ExecutionStep
from app.tools.registry import ToolRegistry
from app.utils.request_context import get_request_id

logger = logging.getLogger(__name__)


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
        synthesizer: ResultSynthesizer | None = None,
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
        self.synthesizer = synthesizer or ResultSynthesizer()

    async def handle(self, request: ChatRequest) -> ChatResponse:
        request_id = get_request_id()
        session = self.session_manager.get_or_create(request.session_id)

        route = self.router.route(request.message)
        context = await self.context_builder.build(
            session=session,
            message=request.message,
            route=route,
        )
        plan = self.planner.make_plan(context=context, route=route)

        logger.info(
            "orchestrator_handle_start request_id=%s session_id=%s route=%s message_len=%s plan_steps=%s",
            request_id,
            session.session_id,
            route,
            len(request.message),
            len(plan),
        )

        execution_log: list[ExecutionStep] = []
        llm_reply = ""

        for index, step in enumerate(plan, start=1):
            llm_reply = await self._execute_step(
                step=step,
                index=index,
                session_id=session.session_id,
                current_reply=llm_reply,
                execution_log=execution_log,
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
            execution_log.append(ExecutionStep(name="verification", status="ok", payload={}))

        await self._save_memory(request=request, session_id=session.session_id, route=route, llm_reply=llm_reply)

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
        current_reply: str,
        execution_log: list[ExecutionStep],
    ) -> str:
        kind = step.get("kind")
        logger.info("execution_step_start session_id=%s index=%s kind=%s", session_id, index, kind)

        if kind == "tool":
            await self._execute_tool_step(step=step, index=index, session_id=session_id, execution_log=execution_log)
            return current_reply

        if kind == "llm":
            return await self._execute_llm_step(
                step=step,
                index=index,
                session_id=session_id,
                execution_log=execution_log,
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
            tool_result = await self.dispatcher.execute(step)

            result_payload = tool_result["result"]
            status = "ok"

            if isinstance(result_payload, dict) and result_payload.get("returncode") not in (None, 0):
                status = "failed"

            execution_log.append(
                ExecutionStep(
                    name=tool_result["tool"],
                    status=status,
                    payload=result_payload,
                )
            )

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
            execution_log: list[ExecutionStep],
    ) - > str:
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
            llm_reply = await self.llm_provider.chat(**args)
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

        execution_log.append(
            ExecutionStep(
                name="llm_chat",
                status="ok",
                payload={"reply_preview": llm_reply[:200]},
            )
        )
        logger.info(
            "execution_step_done session_id=%s index=%s kind=llm reply_len=%s",
            session_id,
            index,
            len(llm_reply),
        )
        return llm_reply

    async def _save_memory(self, *, request: ChatRequest, session_id: str, route: str, llm_reply: str) -> None:
        try:
            await self.memory_service.save(
                {
                    "kind": "interaction",
                    "user_message": request.message,
                    "assistant_reply": llm_reply,
                    "route": route,
                    "metadata": request.metadata,
                    "project_path": request.project_path,
                },
                session_id=session_id,
            )
        except Exception as exc:
            logger.warning(
                "memory_save_failed session_id=%s error=%s",
                session_id,
                exc.__class__.__name__,
            )
