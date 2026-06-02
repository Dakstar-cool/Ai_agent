from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.base import ITool
from app.tools.git._runner import GitReadOnlyRunner


class GitStatusTool(ITool):
    name = "git_status"
    description = "Run read-only git status"

    def __init__(
        self,
        root_dir: str | Path,
        timeout_seconds: float = 15.0,
        max_output_chars: int = 20_000,
    ) -> None:
        self.runner = GitReadOnlyRunner(root_dir, timeout_seconds, max_output_chars)

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        porcelain = kwargs.get("porcelain", True)
        args = ["git", "status", "--short"] if porcelain else ["git", "status"]
        return await self.runner.run(args)
