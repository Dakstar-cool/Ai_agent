from __future__ import annotations

from pathlib import Path

from app.errors import ToolInputError


def resolve_workspace_path(root_dir: Path, value: str, *, must_exist: bool = False) -> Path:
    root = root_dir.resolve()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise ToolInputError("Invalid path", details={"path": value, "error": exc.__class__.__name__}) from exc

    if not resolved.is_relative_to(root):
        raise ToolInputError(
            "Path is outside the allowed workspace",
            details={"path": str(resolved), "workspace_root": str(root)},
        )

    return resolved
