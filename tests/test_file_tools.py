from __future__ import annotations

import pytest

from app.errors import AppError, ToolInputError
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


@pytest.mark.asyncio
async def test_write_file_preview_and_apply_are_hash_bound(tmp_path) -> None:
    tool = WriteFileTool(root_dir=tmp_path)

    preview = await tool.preview(path="note.txt", content="first\n", mode="create")
    preview["preview_hash"] = "a" * 64
    result = await tool.apply_preview(
        mutation_preview=preview,
        path="note.txt",
        content="first\n",
        mode="create",
    )

    assert preview["operation"] == "create"
    assert preview["original_sha256"] is None
    assert "+++ b/note.txt" in preview["unified_diff"]
    assert result["new_sha256"] == preview["new_sha256"]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "first\n"


@pytest.mark.asyncio
async def test_write_file_rejects_stale_preview_without_overwrite(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("original\n", encoding="utf-8")
    tool = WriteFileTool(root_dir=tmp_path)
    preview = await tool.preview(
        path="note.txt",
        content="approved\n",
        mode="overwrite",
    )
    path.write_text("changed elsewhere\n", encoding="utf-8")

    with pytest.raises(AppError) as error:
        await tool.apply_preview(
            mutation_preview=preview,
            path="note.txt",
            content="approved\n",
            mode="overwrite",
        )

    assert error.value.code == "stale_preview"
    assert path.read_text(encoding="utf-8") == "changed elsewhere\n"
