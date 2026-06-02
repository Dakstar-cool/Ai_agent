from __future__ import annotations

import pytest

from app.errors import ToolInputError
from app.tools.files.read_file import ReadFileTool
from app.tools.files.write_file import WriteFileTool


@pytest.mark.asyncio
async def test_read_file_returns_structured_result(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")

    result = await ReadFileTool(root_dir=tmp_path).run(path="note.txt")

    assert result["path"] == str(path)
    assert result["size"] == 5
    assert result["truncated"] is False
    assert result["content"] == "hello"


@pytest.mark.asyncio
async def test_read_file_truncates_to_max_bytes(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("abcdef", encoding="utf-8")

    result = await ReadFileTool(root_dir=tmp_path, max_bytes=3).run(path="note.txt")

    assert result["content"] == "abc"
    assert result["size"] == 6
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_read_file_rejects_binary_file(tmp_path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"abc\x00def")

    with pytest.raises(ToolInputError):
        await ReadFileTool(root_dir=tmp_path).run(path="blob.bin")


@pytest.mark.asyncio
async def test_write_file_create_and_overwrite_modes(tmp_path) -> None:
    tool = WriteFileTool(root_dir=tmp_path)

    created = await tool.run(path="note.txt", content="first")
    assert created["written"] is True
    assert created["mode"] == "create"
    assert created["size"] == 5

    with pytest.raises(ToolInputError):
        await tool.run(path="note.txt", content="second")

    overwritten = await tool.run(path="note.txt", content="second", mode="overwrite")
    assert overwritten["mode"] == "overwrite"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "second"
