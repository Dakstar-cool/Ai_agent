from __future__ import annotations

import sys

import pytest

from app.errors import ToolInputError
from app.tools.terminal.run_command import RunCommandTool


@pytest.mark.asyncio
async def test_run_command_allows_safe_git_status_pattern(tmp_path) -> None:
    tool = RunCommandTool(
        root_dir=tmp_path, allowed_commands={"git"}, timeout_seconds=20
    )
    result = await tool.run(args=["git", "status"])

    assert result["exit_code"] != -1
    assert result["timed_out"] is False
    assert isinstance(result["duration_ms"], float)


@pytest.mark.asyncio
async def test_run_command_blocks_python_c(tmp_path) -> None:
    tool = RunCommandTool(root_dir=tmp_path, allowed_commands={"python"})

    with pytest.raises(ToolInputError):
        await tool.run(args=[sys.executable, "-c", "print('unsafe')"])


@pytest.mark.asyncio
async def test_run_command_blocks_uv_pip(tmp_path) -> None:
    tool = RunCommandTool(root_dir=tmp_path, allowed_commands={"uv"})

    with pytest.raises(ToolInputError):
        await tool.run(args=["uv", "pip", "install", "x"])


@pytest.mark.asyncio
async def test_run_command_blocks_unsafe_git_log_flag(tmp_path) -> None:
    tool = RunCommandTool(root_dir=tmp_path, allowed_commands={"git"})

    with pytest.raises(ToolInputError):
        await tool.run(args=["git", "log", "--output=log.txt"])
