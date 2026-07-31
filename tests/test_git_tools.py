import asyncio
import subprocess
from pathlib import Path

import pytest

from app.tools.git.diff import GitDiffTool
from app.tools.git.log import GitLogTool
from app.tools.git.status import GitStatusTool


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_git_status_is_read_only(tmp_path) -> None:
    await asyncio.to_thread(_git_init, tmp_path)
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    result = await GitStatusTool(root_dir=tmp_path).run()

    assert result["command"] == ["git", "status", "--short"]
    assert "note.txt" in result["stdout"]


@pytest.mark.asyncio
async def test_git_diff_is_read_only(tmp_path) -> None:
    await asyncio.to_thread(_git_init, tmp_path)

    result = await GitDiffTool(root_dir=tmp_path).run()

    assert result["command"] == ["git", "diff"]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_git_log_uses_fixed_read_only_command() -> None:
    root = Path(__file__).resolve().parent.parent

    result = await GitLogTool(root_dir=root).run(max_count=1)

    assert result["command"] == ["git", "log", "--oneline", "-n1"]
    assert "commit" not in result["command"]
