from __future__ import annotations

import pytest

from app.errors import ToolInputError
from app.tools.files.read_file import ReadFileTool
from app.tools.files.write_file import WriteFileTool
from app.tools.terminal.run_command import RunCommandTool


@pytest.mark.asyncio
async def test_read_file_rejects_paths_outside_workspace(tmp_path) -> None:
    tool = ReadFileTool(root_dir=tmp_path)

    with pytest.raises(ToolInputError):
        await tool.run(path="../outside.txt")


@pytest.mark.asyncio
async def test_write_file_rejects_large_content(tmp_path) -> None:
    tool = WriteFileTool(root_dir=tmp_path, max_bytes=3)

    with pytest.raises(ToolInputError):
        await tool.run(path="note.txt", content="too large")


@pytest.mark.asyncio
async def test_run_command_rejects_shell_operators(tmp_path) -> None:
    tool = RunCommandTool(root_dir=tmp_path, allowed_commands={"git"})

    with pytest.raises(ToolInputError):
        await tool.run(command="git status | more")
