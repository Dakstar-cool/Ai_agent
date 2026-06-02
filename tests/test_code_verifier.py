from __future__ import annotations

import pytest

from app.orchestrator.verification.code_verifier import CodeVerifier


@pytest.mark.asyncio
async def test_code_verifier_returns_structured_checks(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = CodeVerifier(root_dir=tmp_path)

    async def fake_run_check(name: str, command: list[str]) -> dict:
        return {
            "name": name,
            "ok": True,
            "skipped": False,
            "command": command,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 1.0,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(verifier, "_run_check", fake_run_check)
    monkeypatch.setattr("app.orchestrator.verification.code_verifier.shutil.which", lambda name: None)

    result = await verifier.verify()

    assert result["ok"] is True
    assert [check["name"] for check in result["checks"]] == ["compileall", "pytest", "ruff"]
    assert result["checks"][2]["skipped"] is True
