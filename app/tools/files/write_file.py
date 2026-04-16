from pathlib import Path
from typing import Any

from app.tools.base import ITool


class WriteFileTool(ITool):
    name = "write_file"
    description = "Write text to a UTF-8 file"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        path = Path(kwargs["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(kwargs["content"], encoding="utf-8")
        return {"path": str(path), "written": True}
