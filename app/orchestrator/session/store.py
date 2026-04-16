from datetime import datetime
from app.orchestrator.models import SessionState, SessionMessage

class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    async def get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        session = await self.get_or_create(session_id)
        session.messages.append(SessionMessage(role=role, content=content))
        session.updated_at = datetime.utcnow()