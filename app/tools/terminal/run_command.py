import asyncio
import os
import shlex
import time
from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import resolve_workspace_path


class RunCommandTool(ITool):
    name = "run_command"
    description = "Execute an allow-listed command without a shell"

    blocked_executables = frozenset(
        {
            "cmd",
            "powershell",
            "bash",
            "sh",
            "curl",
            "wget",
            "ssh",
            "scp",
            "rm",
            "del",
        }
    )

    def __init__(
        self,
        root_dir: str | Path,
        allowed_commands: set[str],
        timeout_seconds: float = 30.0,
        max_output_chars: int = 20_000,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.allowed_commands = {self._normalize_command_name(command) for command in allowed_commands}
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        args = self._parse_args(kwargs)
        self._validate_command(args)

        cwd = kwargs.get("cwd")
        working_dir = self.root_dir
        if cwd is not None:
            if not isinstance(cwd, str) or not cwd.strip():
                raise ToolInputError("Working directory must be a non-empty string")
            working_dir = resolve_workspace_path(self.root_dir, cwd, must_exist=True)
            if not working_dir.is_dir():
                raise ToolInputError("Working directory is not a directory", details={"cwd": str(working_dir)})

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            stdout_text, stdout_truncated = self._decode_output(stdout)
            stderr_text, stderr_truncated = self._decode_output(stderr)
            return {
                "ok": False,
                "command": args,
                "cwd": str(working_dir),
                "exit_code": -1,
                "returncode": -1,
                "stdout": stdout_text,
                "stderr": stderr_text or f"Command timed out after {self.timeout_seconds} seconds",
                "duration_ms": duration_ms,
                "timed_out": True,
                "truncated": stdout_truncated or stderr_truncated,
            }

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        stdout_text, stdout_truncated = self._decode_output(stdout)
        stderr_text, stderr_truncated = self._decode_output(stderr)
        return {
            "ok": process.returncode == 0,
            "command": args,
            "cwd": str(working_dir),
            "exit_code": process.returncode,
            "returncode": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "duration_ms": duration_ms,
            "timed_out": False,
            "truncated": stdout_truncated or stderr_truncated,
        }

    def _parse_args(self, kwargs: dict[str, Any]) -> list[str]:
        raw_args = kwargs.get("args")
        if raw_args is not None:
            if not isinstance(raw_args, list) or not all(isinstance(item, str) and item for item in raw_args):
                raise ToolInputError("Command args must be a non-empty list of strings")
            return raw_args

        command = kwargs.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolInputError("Command is required")

        if any(char in command for char in "|&;<>()`"):
            raise ToolInputError("Shell control operators are not allowed")

        args = shlex.split(command, posix=os.name != "nt")
        args = [item.strip("\"'") for item in args if item.strip("\"'")]
        if not args:
            raise ToolInputError("Command is empty")
        return args

    def _decode_output(self, value: bytes) -> tuple[str, bool]:
        text = value.decode("utf-8", errors="replace")
        if len(text) <= self.max_output_chars:
            return text, False
        return text[:self.max_output_chars] + "\n...[truncated]", True

    def _normalize_command_name(self, value: str) -> str:
        name = Path(value).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        return name

    def _validate_command(self, args: list[str]) -> None:
        executable = self._normalize_command_name(args[0])
        if executable in self.blocked_executables:
            raise ToolInputError("Command is blocked", details={"command": executable})
        if executable not in self.allowed_commands:
            raise ToolInputError(
                "Command is not allowed",
                details={"command": executable, "allowed_commands": sorted(self.allowed_commands)},
            )

        lowered = [item.lower() for item in args]
        if self._is_blocked_subcommand(lowered):
            raise ToolInputError("Command subcommand is blocked", details={"command": args})
        if not self._is_allowed_pattern(lowered):
            raise ToolInputError("Command pattern is not allowed", details={"command": args})

    def _is_blocked_subcommand(self, args: list[str]) -> bool:
        executable = self._normalize_command_name(args[0])
        if executable == "git" and len(args) > 1 and args[1] in {"push", "commit", "reset", "clean"}:
            return True
        if executable == "python" and len(args) > 1:
            return args[1] == "-c" or args[1:3] == ["-m", "pip"]
        if executable == "uv" and len(args) > 1:
            return args[1] in {"add", "remove", "pip"}
        return False

    def _is_allowed_pattern(self, args: list[str]) -> bool:
        executable = self._normalize_command_name(args[0])
        if executable == "git":
            return self._is_allowed_git_pattern(args)
        if executable == "uv":
            return args == ["uv", "run", "pytest", "-q"] or args == [
                "uv",
                "run",
                "python",
                "-m",
                "compileall",
                "app",
                "tests",
            ]
        if executable == "ruff":
            return args == ["ruff", "check", "."]
        return False

    def _is_allowed_git_pattern(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False

        subcommand = args[1]
        rest = args[2:]
        if subcommand == "status":
            return all(item in {"--short", "--porcelain", "--branch"} for item in rest)
        if subcommand == "diff":
            if not rest:
                return True
            return rest[0] == "--" and all(not item.startswith("-") for item in rest[1:])
        if subcommand == "log":
            return all(item == "--oneline" or item.startswith("-n") or item.startswith("--max-count=") for item in rest)
        return False
