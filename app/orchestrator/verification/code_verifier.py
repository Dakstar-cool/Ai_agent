from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.tools.terminal.run_command import RunCommandTool


class CodeVerifier:
    def __init__(
        self,
        root_dir: str | Path,
        timeout_seconds: float = 60.0,
        max_output_chars: int = 20_000,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.tool = RunCommandTool(
            root_dir=self.root_dir,
            allowed_commands={"uv", "ruff"},
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    async def verify(self) -> dict[str, Any]:
        checks = [
            await self._run_check(
                "compileall",
                ["uv", "run", "python", "-m", "compileall", "app", "tests"],
            ),
            await self._run_check("pytest", ["uv", "run", "pytest", "-q"]),
        ]

        if shutil.which("ruff"):
            checks.append(await self._run_check("ruff", ["ruff", "check", "."]))
        else:
            checks.append(
                {
                    "name": "ruff",
                    "ok": True,
                    "skipped": True,
                    "reason": "ruff executable not found",
                    "command": ["ruff", "check", "."],
                }
            )

        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    async def _run_check(self, name: str, command: list[str]) -> dict[str, Any]:
        result = await self.tool.run(args=command)
        return {
            "name": name,
            "ok": result["ok"],
            "skipped": False,
            "command": result["command"],
            "exit_code": result["exit_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "duration_ms": result["duration_ms"],
            "timed_out": result["timed_out"],
            "truncated": result["truncated"],
        }
