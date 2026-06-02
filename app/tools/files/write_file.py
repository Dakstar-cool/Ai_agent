from pathlib import Path
import os
import tempfile
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
        mode = kwargs.get("mode", "create")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolInputError("File path is required")
        if not isinstance(content, str):
            raise ToolInputError("File content must be a string")
        if mode not in {"create", "overwrite"}:
            raise ToolInputError("Write mode must be either create or overwrite", details={"mode": mode})

        content_size = len(content.encode("utf-8"))
        if content_size > self.max_bytes:
            raise ToolInputError(
                "File content is too large to write",
                details={"size": content_size, "max_bytes": self.max_bytes},
            )

        path = resolve_workspace_path(self.root_dir, raw_path)
        if path.exists() and mode != "overwrite":
            raise ToolInputError(
                "File already exists; use mode=overwrite to replace it",
                details={"path": str(path), "mode": mode},
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)
        return {"path": str(path), "written": True, "mode": mode, "size": content_size}

    def _atomic_write(self, path: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
