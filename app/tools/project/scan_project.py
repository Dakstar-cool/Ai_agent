from __future__ import annotations

from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import IGNORED_DIRS, WorkspacePathPolicy


class ScanProjectTool(ITool):
    name = "scan_project"
    description = "List project files while skipping protected and ignored directories"

    def __init__(self, root_dir: str | Path, max_files: int = 500) -> None:
        self.policy = WorkspacePathPolicy(Path(root_dir))
        self.max_files = max_files

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        raw_path = kwargs.get("path", ".")
        max_files = kwargs.get("max_files", self.max_files)
        if not isinstance(max_files, int) or max_files <= 0:
            raise ToolInputError("max_files must be a positive integer")

        root = self.policy.resolve(raw_path, must_exist=True)
        if not root.is_dir():
            raise ToolInputError("Scan path must be a directory", details={"path": str(root)})

        files: list[dict[str, Any]] = []
        truncated = False
        for item in sorted(root.rglob("*")):
            if self.policy.is_ignored_path(item, ignored_dirs=IGNORED_DIRS):
                continue
            if not item.is_file():
                continue
            files.append(
                {
                    "path": item.relative_to(self.policy.root_dir).as_posix(),
                    "size": item.stat().st_size,
                }
            )
            if len(files) >= max_files:
                truncated = True
                break

        return {"root": str(root), "files": files, "count": len(files), "truncated": truncated}
