import asyncio
from typing import Any

from app.tools.base import ITool


class RunCommandTool(ITool):
    name = "run_command"
    description = "Execute a shell command"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        command = kwargs["command"]
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return {
            "command": command,
            "returncode": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }
