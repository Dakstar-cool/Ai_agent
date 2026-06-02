from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.errors import ToolInputError


PROTECTED_PATH_PARTS = frozenset(
    {
        ".env",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
        "dist",
        "build",
    }
)

IGNORED_DIRS = PROTECTED_PATH_PARTS | frozenset({".idea", "ai_agentv1.egg-info"})


@dataclass(frozen=True, slots=True)
class WorkspacePathPolicy:
    root_dir: Path
    protected_parts: frozenset[str] = field(default_factory=lambda: PROTECTED_PATH_PARTS)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_dir", Path(self.root_dir).resolve())
        object.__setattr__(self, "protected_parts", frozenset(part.lower() for part in self.protected_parts))

    def resolve(self, value: str, *, must_exist: bool = False, allow_protected: bool = False) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ToolInputError("Path must be a non-empty string")

        root = self.root_dir
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

        if not allow_protected:
            protected_part = self.first_protected_part(resolved)
            if protected_part is not None:
                raise ToolInputError(
                    "Path is protected and cannot be accessed by tools",
                    details={"path": str(resolved), "protected_part": protected_part},
                )

        return resolved

    def first_protected_part(self, path: Path) -> str | None:
        resolved = path.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root_dir)
        except ValueError:
            return None

        for part in relative.parts:
            if part.lower() in self.protected_parts:
                return part
        return None

    def is_ignored_path(self, path: Path, *, ignored_dirs: set[str] | frozenset[str] = IGNORED_DIRS) -> bool:
        ignored = {item.lower() for item in ignored_dirs} | self.protected_parts
        try:
            relative = path.resolve(strict=False).relative_to(self.root_dir)
        except ValueError:
            return True
        return any(part.lower() in ignored for part in relative.parts)


def resolve_workspace_path(root_dir: Path, value: str, *, must_exist: bool = False, allow_protected: bool = False) -> Path:
    return WorkspacePathPolicy(root_dir).resolve(value, must_exist=must_exist, allow_protected=allow_protected)


def is_probably_binary_file(path: Path, *, sample_size: int = 4096) -> bool:
    sample = path.read_bytes()[:sample_size]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def iter_safe_files(root: Path, *, ignored_dirs: set[str] | frozenset[str] = IGNORED_DIRS):
    policy = WorkspacePathPolicy(root)
    for item in policy.root_dir.rglob("*"):
        if policy.is_ignored_path(item, ignored_dirs=ignored_dirs):
            continue
        if item.is_file():
            yield item
