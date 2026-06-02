from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.base import ITool
from app.tools.git._runner import GitReadOnlyRunner
from app.tools.path_safety import WorkspacePathPolicy


class GitDiffTool(ITool):
    name = "git_diff"
    description = "Run read-only git diff"

    def __init__(self, root_dir: str | Path, timeout_seconds: float = 15.0, max_output_chars: int = 20_000) -> None:
        self.policy = WorkspacePathPolicy(Path(root_dir))
        self.runner = GitReadOnlyRunner(root_dir, timeout_seconds, max_output_chars)

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        args = ["git", "diff"]
        raw_path = kwargs.get("path")
        if raw_path is not None:
            path = self.policy.resolve(raw_path, must_exist=False)
            args.extend(["--", path.relative_to(self.policy.root_dir).as_posix()])
        return await self.runner.run(args)
