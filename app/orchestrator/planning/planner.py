from typing import Any


class Planner:
    def make_plan(self, context: dict[str, Any], route: str) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []

        if route == "coding":
            plan.append({"kind": "tool", "tool_name": "run_command", "args": {"command": "cd"}})

        plan.append({"kind": "llm", "args": {"messages": self._build_messages(context)}})
        return plan

    def _build_messages(self, context: dict[str, Any]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": context["system_prompt"]}]

        for item in context["history"]:
            messages.append({"role": item["role"], "content": item["content"]})

        if context["memories"]:
            memory_block = "\n".join(f"- {self._format_memory(m)}" for m in context["memories"])
            messages.append({"role": "system", "content": f"Relevant memories:\n{memory_block}"})

        messages.append({"role": "user", "content": context["user_message"]})
        return messages

    def _format_memory(self, memory: Any) -> str:
        if isinstance(memory, str):
            return memory
        if isinstance(memory, dict):
            if isinstance(memory.get("summary"), str) and memory["summary"].strip():
                return memory["summary"]
            user_message = str(memory.get("user_message", "")).strip()
            assistant_reply = str(memory.get("assistant_reply", "")).strip()
            if user_message or assistant_reply:
                return f"user={user_message} | assistant={assistant_reply[:180]}"
        return str(memory)
