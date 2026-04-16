from app.tools.base import ITool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ITool] = {}

    def register(self, tool: ITool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ITool:
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")
        return self._tools[name]

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())
