from pathlib import Path
from typing import Any

from app.tools.base import ITool


class ReadFileTool(ITool):
    name = "read_file"
    description = "Read a UTF-8 text file"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        path = Path(kwargs["path"])
        content = path.read_text(encoding="utf-8")
        return {"path": str(path), "content": content}
