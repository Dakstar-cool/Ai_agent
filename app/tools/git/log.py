from __future__ import annotations

from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.git._runner import GitReadOnlyRunner


class GitLogTool(ITool):
    name = "git_log"
    description = "Run read-only git log"

    def __init__(
        self,
        root_dir: str | Path,
        timeout_seconds: float = 15.0,
        max_output_chars: int = 20_000,
    ) -> None:
        self.runner = GitReadOnlyRunner(root_dir, timeout_seconds, max_output_chars)

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        max_count = kwargs.get("max_count", 20)
        if not isinstance(max_count, int) or max_count <= 0 or max_count > 100:
            raise ToolInputError("max_count must be an integer from 1 to 100")
        return await self.runner.run(["git", "log", "--oneline", f"-n{max_count}"])
