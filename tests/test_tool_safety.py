from __future__ import annotations

import pytest

from app.errors import ToolInputError
from app.tools.files.read_file import ReadFileTool
from app.tools.files.write_file import WriteFileTool
from app.tools.path_safety import WorkspacePathPolicy
from app.tools.terminal.run_command import RunCommandTool


@pytest.mark.asyncio
async def test_read_file_rejects_paths_outside_workspace(tmp_path) -> None:
    tool = ReadFileTool(root_dir=tmp_path)

    with pytest.raises(ToolInputError):
        await tool.run(path="../outside.txt")


def test_path_policy_rejects_traversal(tmp_path) -> None:
    policy = WorkspacePathPolicy(tmp_path)

    with pytest.raises(ToolInputError):
        policy.resolve("..")


def test_path_policy_rejects_protected_path_parts(tmp_path) -> None:
    protected_file = tmp_path / ".env"
    protected_file.write_text("SECRET=1", encoding="utf-8")
    policy = WorkspacePathPolicy(tmp_path)

    with pytest.raises(ToolInputError):
        policy.resolve(".env", must_exist=True)

    with pytest.raises(ToolInputError):
        policy.resolve(".git/config", must_exist=False)


def test_path_policy_rejects_symlinks_even_when_target_is_inside_workspace(
    tmp_path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")

    with pytest.raises(ToolInputError, match="Symbolic links"):
        WorkspacePathPolicy(tmp_path).resolve("linked.txt", must_exist=True)


@pytest.mark.asyncio
async def test_approved_write_rejects_symlink_swap_before_mutation(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "note.txt"
    path.write_text("original\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    tool = WriteFileTool(root_dir=workspace)
    preview = await tool.preview(
        path="note.txt",
        content="approved\n",
        mode="overwrite",
    )
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")

    with pytest.raises(ToolInputError, match="Symbolic links"):
        await tool.apply_preview(
            mutation_preview=preview,
            path="note.txt",
            content="approved\n",
            mode="overwrite",
        )

    assert outside.read_text(encoding="utf-8") == "outside\n"


@pytest.mark.asyncio
async def test_write_file_rejects_large_content(tmp_path) -> None:
    tool = WriteFileTool(root_dir=tmp_path, max_bytes=3)

    with pytest.raises(ToolInputError):
        await tool.run(path="note.txt", content="too large")


@pytest.mark.asyncio
async def test_write_file_rejects_protected_paths(tmp_path) -> None:
    tool = WriteFileTool(root_dir=tmp_path)

    with pytest.raises(ToolInputError):
        await tool.run(path=".env", content="SECRET=1")


@pytest.mark.asyncio
async def test_run_command_rejects_shell_operators(tmp_path) -> None:
    tool = RunCommandTool(root_dir=tmp_path, allowed_commands={"git"})

    with pytest.raises(ToolInputError):
        await tool.run(command="git status | more")


@pytest.mark.asyncio
async def test_run_command_rejects_dangerous_git_subcommand(tmp_path) -> None:
    tool = RunCommandTool(root_dir=tmp_path, allowed_commands={"git"})

    with pytest.raises(ToolInputError):
        await tool.run(args=["git", "push"])
