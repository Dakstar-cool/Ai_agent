from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from app.errors import AppError
from app.providers.llm.models import ToolCall
from app.state.store import SQLiteStateStore


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval_id: str
    run_id: str | None
    session_id: str
    tool_call: ToolCall
    route: str
    project_path: str | None
    approval_hash: str
    mutation_preview: dict[str, object] | None
    created_at: float
    expires_at: float


class PendingApprovalStore:
    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_pending: int = 200,
        clock: Callable[[], float] = time.time,
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
        run_id: str | None = None,
        mutation_preview: dict[str, object] | None = None,
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

        approval_id = uuid4().hex
        expires_at = now + self.ttl_seconds
        finalized_preview = self._finalize_mutation_preview(
            mutation_preview,
            approval_id=approval_id,
            expires_at=expires_at,
        )
        approval_hash = (
            str(finalized_preview["preview_hash"])
            if finalized_preview is not None
            else self._approval_hash(tool_call)
        )
        pending = PendingApproval(
            approval_id=approval_id,
            run_id=run_id,
            session_id=session_id,
            tool_call=tool_call.model_copy(deep=True),
            route=route,
            project_path=project_path,
            approval_hash=approval_hash,
            mutation_preview=finalized_preview,
            created_at=now,
            expires_at=expires_at,
        )
        self._pending[pending.approval_id] = pending
        return pending

    def get(self, approval_id: str) -> PendingApproval:
        now = self._clock()
        pending = self._pending.get(approval_id)
        if pending is None:
            self._remove_expired(now)
            raise AppError(
                message="Pending approval was not found",
                code="approval_not_found",
                status_code=404,
            )
        if pending.expires_at <= now:
            del self._pending[approval_id]
            raise AppError(
                message="Pending approval has expired",
                code="approval_expired",
                status_code=409,
            )
        return pending

    def bind_to_run(self, *, approval_id: str, run_id: str) -> None:
        pending = self.get(approval_id)
        self._pending[approval_id] = replace(pending, run_id=run_id)

    def validate_hash(self, *, approval_id: str, expected_hash: str) -> None:
        pending = self.get(approval_id)
        if pending.approval_hash != expected_hash:
            raise AppError(
                message="Approval hash does not match the pending action",
                code="approval_hash_mismatch",
                status_code=409,
            )

    def reject(self, *, approval_id: str, expected_hash: str) -> PendingApproval:
        self.validate_hash(approval_id=approval_id, expected_hash=expected_hash)
        return self._pending.pop(approval_id)

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

    def _approval_hash(self, tool_call: ToolCall) -> str:
        canonical = json.dumps(
            tool_call.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _finalize_mutation_preview(
        self,
        preview: dict[str, object] | None,
        *,
        approval_id: str,
        expires_at: float,
    ) -> dict[str, object] | None:
        if preview is None:
            return None
        finalized: dict[str, object] = {
            "schema_version": "0.3.0",
            "preview_id": approval_id,
            **preview,
            "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        }
        canonical = json.dumps(
            finalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        finalized["preview_hash"] = sha256(canonical.encode("utf-8")).hexdigest()
        return finalized


class SQLitePendingApprovalStore(PendingApprovalStore):
    def __init__(
        self,
        *,
        state_store: SQLiteStateStore,
        ttl_seconds: float = 300.0,
        max_pending: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            ttl_seconds=ttl_seconds,
            max_pending=max_pending,
            clock=clock,
        )
        self.state_store = state_store

    def create(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        route: str,
        project_path: str | None,
        run_id: str | None = None,
        mutation_preview: dict[str, object] | None = None,
    ) -> PendingApproval:
        now = self._clock()
        created_at = datetime.fromtimestamp(now, UTC)
        approval_id = uuid4().hex
        expires_at = now + self.ttl_seconds
        finalized_preview = self._finalize_mutation_preview(
            mutation_preview,
            approval_id=approval_id,
            expires_at=expires_at,
        )
        approval_hash = (
            str(finalized_preview["preview_hash"])
            if finalized_preview is not None
            else self._approval_hash(tool_call)
        )
        record = self.state_store.create_approval_record(
            approval_id=approval_id,
            run_id=run_id,
            session_id=session_id,
            tool_call=tool_call.model_dump(mode="json"),
            route=route,
            project_path=project_path,
            approval_hash=approval_hash,
            mutation_preview=finalized_preview,
            created_at=created_at,
            expires_at=datetime.fromtimestamp(expires_at, UTC),
            max_pending=self.max_pending,
        )
        return self._from_record(record)

    def get(self, approval_id: str) -> PendingApproval:
        record = self.state_store.get_approval_record(approval_id)
        if record is None or record["state"] != "pending":
            raise AppError(
                message="Pending approval was not found",
                code="approval_not_found",
                status_code=404,
            )
        pending = self._from_record(record)
        if pending.expires_at <= self._clock():
            self.state_store.consume_approval_record(
                approval_id=approval_id,
                session_id=pending.session_id,
                now=datetime.fromtimestamp(self._clock(), UTC),
            )
        return pending

    def consume(self, *, approval_id: str, session_id: str) -> PendingApproval:
        record = self.state_store.consume_approval_record(
            approval_id=approval_id,
            session_id=session_id,
            now=datetime.fromtimestamp(self._clock(), UTC),
        )
        return self._from_record(record)

    def bind_to_run(self, *, approval_id: str, run_id: str) -> None:
        self.state_store.bind_approval_to_run(
            approval_id=approval_id,
            run_id=run_id,
        )

    def validate_hash(self, *, approval_id: str, expected_hash: str) -> None:
        self.state_store.validate_approval_hash(
            approval_id=approval_id,
            expected_hash=expected_hash,
        )

    def reject(self, *, approval_id: str, expected_hash: str) -> PendingApproval:
        record = self.state_store.reject_approval_record(
            approval_id=approval_id,
            expected_hash=expected_hash,
            now=datetime.fromtimestamp(self._clock(), UTC),
        )
        return self._from_record(record)

    def remaining_seconds(self, pending: PendingApproval) -> float:
        return max(0.0, pending.expires_at - self._clock())

    def _from_record(self, record: dict[str, object]) -> PendingApproval:
        created_at = record["created_at"]
        expires_at = record["expires_at"]
        if not isinstance(created_at, datetime) or not isinstance(expires_at, datetime):
            raise TypeError("Stored approval timestamps are invalid")
        return PendingApproval(
            approval_id=str(record["approval_id"]),
            run_id=str(record["run_id"]) if record["run_id"] is not None else None,
            session_id=str(record["session_id"]),
            tool_call=ToolCall.model_validate(record["tool_call"]),
            route=str(record["route"]),
            project_path=(
                str(record["project_path"])
                if record["project_path"] is not None
                else None
            ),
            approval_hash=str(record["approval_hash"]),
            mutation_preview=(
                dict(record["mutation_preview"])
                if isinstance(record["mutation_preview"], dict)
                else None
            ),
            created_at=created_at.timestamp(),
            expires_at=expires_at.timestamp(),
        )
