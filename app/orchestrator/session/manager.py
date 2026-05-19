from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SessionState:
    session_id: str
    history: list[dict[str, str]] = field(default_factory=list)


class SessionManager:
    def __init__(self, max_sessions: int = 200, max_messages: int = 50) -> None:
        self._sessions: dict[str, SessionState] = {}
        self.max_sessions = max(1, max_sessions)
        self.max_messages = max(1, max_messages)

    def get_or_create(self, session_id: str | None) -> SessionState:
        sid = session_id or str(uuid4())
        if sid not in self._sessions:
            self._evict_oldest_session_if_needed()
            self._sessions[sid] = SessionState(session_id=sid)
        return self._sessions[sid]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        session = self.get_or_create(session_id)
        session.history.append({"role": role, "content": content})
        if len(session.history) > self.max_messages:
            del session.history[:-self.max_messages]

    def _evict_oldest_session_if_needed(self) -> None:
        if len(self._sessions) < self.max_sessions:
            return
        oldest_session_id = next(iter(self._sessions))
        del self._sessions[oldest_session_id]
