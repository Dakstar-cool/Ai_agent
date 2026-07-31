from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from app.errors import AppError
from app.providers.llm.models import ToolCall


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval_id: str
    session_id: str
    tool_call: ToolCall
    route: str
    project_path: str | None
    created_at: float
    expires_at: float


class PendingApprovalStore:
    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_pending: int = 200,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = max(0.1, ttl_seconds)
        self.max_pending = max(1, max_pending)
        self._clock = clock
        self._pending: OrderedDict[str, PendingApproval] = OrderedDict()

    def create(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        route: str,
        project_path: str | None,
    ) -> PendingApproval:
        now = self._clock()
        self._remove_expired(now)
        signature = self._signature(tool_call)

        for pending in self._pending.values():
            if (
                pending.session_id == session_id
                and pending.route == route
                and pending.project_path == project_path
                and self._signature(pending.tool_call) == signature
            ):
                return pending

        if len(self._pending) >= self.max_pending:
            self._pending.popitem(last=False)

        pending = PendingApproval(
            approval_id=uuid4().hex,
            session_id=session_id,
            tool_call=tool_call.model_copy(deep=True),
            route=route,
            project_path=project_path,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._pending[pending.approval_id] = pending
        return pending

    def consume(self, *, approval_id: str, session_id: str) -> PendingApproval:
        now = self._clock()
        pending = self._pending.get(approval_id)
        if pending is None or pending.session_id != session_id:
            self._remove_expired(now)
            raise AppError(
                message="Pending approval was not found for this session",
                code="approval_not_found",
                status_code=404,
            )

        if pending.expires_at <= now:
            del self._pending[approval_id]
            self._remove_expired(now)
            raise AppError(
                message="Pending approval has expired",
                code="approval_expired",
                status_code=409,
            )

        del self._pending[approval_id]
        self._remove_expired(now)
        return pending

    def remaining_seconds(self, pending: PendingApproval) -> float:
        return max(0.0, pending.expires_at - self._clock())

    def _remove_expired(self, now: float) -> None:
        expired = [
            approval_id
            for approval_id, pending in self._pending.items()
            if pending.expires_at <= now
        ]
        for approval_id in expired:
            del self._pending[approval_id]

    def _signature(self, tool_call: ToolCall) -> str:
        arguments = json.dumps(
            tool_call.arguments, ensure_ascii=False, sort_keys=True, default=str
        )
        return f"{tool_call.name}:{arguments}"
