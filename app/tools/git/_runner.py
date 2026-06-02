from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any


class GitReadOnlyRunner:
    def __init__(self, root_dir: str | Path, timeout_seconds: float = 15.0, max_output_chars: int = 20_000) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    async def run(self, args: list[str]) -> dict[str, Any]:
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
            timed_out = False
            exit_code = process.returncode
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            timed_out = True
            exit_code = -1

        stdout_text, stdout_truncated = self._decode(stdout)
        stderr_text, stderr_truncated = self._decode(stderr)
        if timed_out and not stderr_text:
            stderr_text = f"Command timed out after {self.timeout_seconds} seconds"

        return {
            "ok": exit_code == 0,
            "command": args,
            "cwd": str(self.root_dir),
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "timed_out": timed_out,
            "truncated": stdout_truncated or stderr_truncated,
        }

    def _decode(self, value: bytes) -> tuple[str, bool]:
        text = value.decode("utf-8", errors="replace")
        if len(text) <= self.max_output_chars:
            return text, False
        return text[: self.max_output_chars] + "\n...[truncated]", True
