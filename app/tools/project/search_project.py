from __future__ import annotations

from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import (
    IGNORED_DIRS,
    WorkspacePathPolicy,
    iter_safe_files,
    is_probably_binary_file,
)


class SearchProjectTool(ITool):
    name = "search_project"
    description = (
        "Search text in project files while skipping protected and ignored directories"
    )

    def __init__(
        self,
        root_dir: str | Path,
        max_results: int = 100,
        max_file_bytes: int = 200_000,
    ) -> None:
        self.policy = WorkspacePathPolicy(Path(root_dir))
        self.max_results = max_results
        self.max_file_bytes = max_file_bytes

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        query = kwargs.get("query")
        raw_path = kwargs.get("path", ".")
        max_results = kwargs.get("max_results", self.max_results)
        if not isinstance(query, str) or not query:
            raise ToolInputError("Search query is required")
        if not isinstance(max_results, int) or max_results <= 0:
            raise ToolInputError("max_results must be a positive integer")

        root = self.policy.resolve(raw_path, must_exist=True)
        if not root.is_dir():
            raise ToolInputError(
                "Search path must be a directory", details={"path": str(root)}
            )

        matches: list[dict[str, Any]] = []
        truncated = False
        query_folded = query.casefold()

        for item in sorted(iter_safe_files(root, ignored_dirs=IGNORED_DIRS)):
            if item.stat().st_size > self.max_file_bytes or is_probably_binary_file(
                item
            ):
                continue

            for line_number, line in enumerate(
                item.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                if query_folded not in line.casefold():
                    continue
                matches.append(
                    {
                        "path": item.relative_to(self.policy.root_dir).as_posix(),
                        "line_number": line_number,
                        "line": line,
                    }
                )
                if len(matches) >= max_results:
                    truncated = True
                    return {
                        "root": str(root),
                        "query": query,
                        "matches": matches,
                        "count": len(matches),
                        "truncated": truncated,
                    }

        return {
            "root": str(root),
            "query": query,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
        }
