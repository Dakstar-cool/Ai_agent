import asyncio
import os
import shlex
from pathlib import Path
from typing import Any

from app.errors import ToolInputError
from app.tools.base import ITool
from app.tools.path_safety import resolve_workspace_path


class RunCommandTool(ITool):
    name = "run_command"
    description = "Execute a shell command"

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
        executable = self._normalize_command_name(args[0])
        if executable not in self.allowed_commands:
            raise ToolInputError(
                "Command is not allowed",
                details={"command": executable, "allowed_commands": sorted(self.allowed_commands)},
            )

        cwd = kwargs.get("cwd")
        working_dir = self.root_dir
        if cwd is not None:
            if not isinstance(cwd, str) or not cwd.strip():
                raise ToolInputError("Working directory must be a non-empty string")
            working_dir = resolve_workspace_path(self.root_dir, cwd, must_exist=True)
            if not working_dir.is_dir():
                raise ToolInputError("Working directory is not a directory", details={"cwd": str(working_dir)})

        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return {
                "command": args,
                "cwd": str(working_dir),
                "returncode": -1,
                "stdout": self._decode_output(stdout),
                "stderr": f"Command timed out after {self.timeout_seconds} seconds",
            }

        return {
            "command": args,
            "cwd": str(working_dir),
            "returncode": process.returncode,
            "stdout": self._decode_output(stdout),
            "stderr": self._decode_output(stderr),
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

    def _decode_output(self, value: bytes) -> str:
        text = value.decode("utf-8", errors="replace")
        if len(text) <= self.max_output_chars:
            return text
        return text[:self.max_output_chars] + "\n...[truncated]"

    def _normalize_command_name(self, value: str) -> str:
        name = Path(value).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        return name
