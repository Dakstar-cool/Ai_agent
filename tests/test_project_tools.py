from __future__ import annotations

import pytest

from app.tools.project.scan_project import ScanProjectTool
from app.tools.project.search_project import SearchProjectTool


@pytest.mark.asyncio
async def test_scan_project_skips_protected_dirs(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")

    result = await ScanProjectTool(root_dir=tmp_path).run()

    paths = {item["path"] for item in result["files"]}
    assert "app/main.py" in paths
    assert ".git/config" not in paths


@pytest.mark.asyncio
async def test_search_project_finds_text_and_skips_protected(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("needle\n", encoding="utf-8")

    result = await SearchProjectTool(root_dir=tmp_path).run(query="needle")

    assert result["count"] == 1
    assert result["matches"][0]["path"] == "app/main.py"
    assert result["matches"][0]["line_number"] == 1
