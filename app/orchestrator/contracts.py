"""Experimental scaffold contracts for the older orchestrator model layer.

The active runtime currently uses app.orchestrator.core plus the concrete
routing/planning/session modules. Keep these contracts as migration reference
only until a later cleanup consolidates the parallel model layer.
"""

from typing import Protocol
from app.orchestrator.models import (
    ExecutionContext,
    ExecutionPlan,
    SessionState,
    TaskType,
)


class IModelProvider(Protocol):
    async def chat(self, messages: list[dict[str, str]]) -> str: ...


class ISessionStore(Protocol):
    async def get_or_create(self, session_id: str) -> SessionState: ...
    async def append_message(
        self, session_id: str, role: str, content: str
    ) -> None: ...


class ITaskRouter(Protocol):
    def route(self, user_message: str) -> TaskType: ...


class IPlanner(Protocol):
    def build_plan(
        self, user_message: str, context: ExecutionContext
    ) -> ExecutionPlan: ...
