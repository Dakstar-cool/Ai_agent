from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import resolve_workspace_path


class ReadFileTool(ITool):
    name = "read_file"
    description = "Read a UTF-8 text file"

    def __init__(self, root_dir: str | Path, max_bytes: int = 200_000) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.max_bytes = max_bytes

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        raw_path = kwargs.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolInputError("File path is required")

        path = resolve_workspace_path(self.root_dir, raw_path, must_exist=True)
        if not path.is_file():
            raise ToolInputError("Path is not a file", details={"path": str(path)})

        size = path.stat().st_size
        if size > self.max_bytes:
            raise ToolInputError(
                "File is too large to read",
                details={"path": str(path), "size": size, "max_bytes": self.max_bytes},
            )

        content = path.read_text(encoding="utf-8")
        return {"path": str(path), "content": content}
