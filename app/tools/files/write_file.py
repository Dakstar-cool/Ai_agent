import difflib
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from typing import Any

from app.errors import AppError, ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import resolve_workspace_path


class WriteFileTool(ITool):
    name = "write_file"
    description = "Write text to a UTF-8 file"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path"},
            "content": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["create", "overwrite"],
                "default": "create",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, root_dir: str | Path, max_bytes: int = 200_000) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.max_bytes = max_bytes

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        validated = self._validate_request(kwargs)
        path = validated["path"]
        content = validated["content"]
        mode = validated["mode"]
        content_size = validated["size"]

        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)
        return {"path": str(path), "written": True, "mode": mode, "size": content_size}

    async def preview(self, **kwargs: Any) -> dict[str, Any]:
        validated = self._validate_request(kwargs)
        path: Path = validated["path"]
        content: str = validated["content"]
        content_bytes = content.encode("utf-8")

        old_content = ""
        original_sha256: str | None = None
        if path.exists():
            old_bytes = path.read_bytes()
            if len(old_bytes) > self.max_bytes:
                raise ToolInputError(
                    "Existing file is too large to preview",
                    details={"size": len(old_bytes), "max_bytes": self.max_bytes},
                )
            try:
                old_content = old_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolInputError("Existing file is not UTF-8 text") from exc
            original_sha256 = sha256(old_bytes).hexdigest()

        relative_path = path.relative_to(self.root_dir).as_posix()
        unified_diff = "".join(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative_path}" if path.exists() else "/dev/null",
                tofile=f"b/{relative_path}",
            )
        )
        return {
            "operation": "overwrite" if path.exists() else "create",
            "path": relative_path,
            "unified_diff": unified_diff,
            "original_sha256": original_sha256,
            "new_sha256": sha256(content_bytes).hexdigest(),
            "size": len(content_bytes),
        }

    async def apply_preview(
        self, *, mutation_preview: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        validated = self._validate_request(kwargs)
        path: Path = validated["path"]
        content: str = validated["content"]
        content_bytes = content.encode("utf-8")

        expected_path = mutation_preview.get("path")
        actual_path = path.relative_to(self.root_dir).as_posix()
        if expected_path != actual_path:
            raise AppError(
                message="Mutation preview path no longer matches",
                code="stale_preview",
                status_code=409,
            )

        current_hash = sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current_hash != mutation_preview.get("original_sha256"):
            raise AppError(
                message="File changed after mutation preview",
                code="stale_preview",
                status_code=409,
            )
        new_hash = sha256(content_bytes).hexdigest()
        if new_hash != mutation_preview.get("new_sha256"):
            raise AppError(
                message="Approved content no longer matches mutation preview",
                code="preview_content_mismatch",
                status_code=409,
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)
        return {
            "path": str(path),
            "written": True,
            "mode": validated["mode"],
            "size": len(content_bytes),
            "preview_hash": mutation_preview.get("preview_hash"),
            "original_sha256": current_hash,
            "new_sha256": new_hash,
        }

    def _validate_request(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        raw_path = kwargs.get("path")
        content = kwargs.get("content")
        mode = kwargs.get("mode", "create")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolInputError("File path is required")
        if not isinstance(content, str):
            raise ToolInputError("File content must be a string")
        if mode not in {"create", "overwrite"}:
            raise ToolInputError(
                "Write mode must be either create or overwrite", details={"mode": mode}
            )

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
        if not path.exists() and mode == "overwrite":
            raise ToolInputError(
                "File does not exist; use mode=create for a new file",
                details={"path": str(path), "mode": mode},
            )
        return {
            "path": path,
            "content": content,
            "mode": mode,
            "size": content_size,
        }

    def _atomic_write(self, path: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
        )
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
