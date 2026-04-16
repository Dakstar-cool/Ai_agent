from typing import Any


class ResultSynthesizer:
    def synthesize(self, llm_reply: str, execution_log: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "reply": llm_reply.strip(),
            "steps": execution_log,
        }
