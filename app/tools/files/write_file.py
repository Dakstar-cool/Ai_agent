from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import resolve_workspace_path


class WriteFileTool(ITool):
    name = "write_file"
    description = "Write text to a UTF-8 file"

    def __init__(self, root_dir: str | Path, max_bytes: int = 200_000) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.max_bytes = max_bytes

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        raw_path = kwargs.get("path")
        content = kwargs.get("content")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolInputError("File path is required")
        if not isinstance(content, str):
            raise ToolInputError("File content must be a string")

        content_size = len(content.encode("utf-8"))
        if content_size > self.max_bytes:
            raise ToolInputError(
                "File content is too large to write",
                details={"size": content_size, "max_bytes": self.max_bytes},
            )

        path = resolve_workspace_path(self.root_dir, raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "written": True}
