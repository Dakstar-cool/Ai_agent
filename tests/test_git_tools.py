from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.tools.git.diff import GitDiffTool
from app.tools.git.log import GitLogTool
from app.tools.git.status import GitStatusTool


@pytest.mark.asyncio
async def test_git_status_is_read_only(tmp_path) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    result = await GitStatusTool(root_dir=tmp_path).run()

    assert result["command"] == ["git", "status", "--short"]
    assert "note.txt" in result["stdout"]


@pytest.mark.asyncio
async def test_git_diff_is_read_only(tmp_path) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    result = await GitDiffTool(root_dir=tmp_path).run()

    assert result["command"] == ["git", "diff"]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_git_log_uses_fixed_read_only_command() -> None:
    root = Path(__file__).resolve().parent.parent

    result = await GitLogTool(root_dir=root).run(max_count=1)

    assert result["command"] == ["git", "log", "--oneline", "-n1"]
    assert "commit" not in result["command"]
