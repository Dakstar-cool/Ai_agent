from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SessionState:
    session_id: str
    history: list[dict[str, str]] = field(default_factory=list)


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_id: str | None) -> SessionState:
        sid = session_id or str(uuid4())
        if sid not in self._sessions:
            self._sessions[sid] = SessionState(session_id=sid)
        return self._sessions[sid]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        self._sessions[session_id].history.append({"role": role, "content": content})
