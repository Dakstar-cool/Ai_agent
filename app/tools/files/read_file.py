from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import is_probably_binary_file, resolve_workspace_path


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
        if is_probably_binary_file(path):
            raise ToolInputError(
                "Binary files cannot be read by this tool", details={"path": str(path)}
            )

        with path.open("rb") as handle:
            raw = handle.read(self.max_bytes + 1)

        truncated = len(raw) > self.max_bytes
        if truncated:
            raw = raw[: self.max_bytes]

        content = raw.decode("utf-8", errors="replace")
        return {
            "path": str(path),
            "size": size,
            "truncated": truncated,
            "content": content,
        }
