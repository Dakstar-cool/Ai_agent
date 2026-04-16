class Verifier:
    def verify(self, reply: str) -> tuple[bool, str | None]:
        if not reply or not reply.strip():
            return False, "Empty reply from model"
        return True, None
