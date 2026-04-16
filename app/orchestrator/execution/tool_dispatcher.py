from typing import Any

from app.tools.registry import ToolRegistry


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, step: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.get(step["tool_name"])
        result = await tool.run(**step.get("args", {}))
        return {"tool": step["tool_name"], "result": result}
