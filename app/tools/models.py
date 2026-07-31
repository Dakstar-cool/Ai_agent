from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


ToolResultStatus = Literal["ok", "failed", "approval_required"]


class ToolResult(BaseModel):
    tool_call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] | None = Field(default=None, exclude=True)

    def to_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": json.dumps(
                {
                    "status": self.status,
                    "trusted": False,
                    "result": self.output,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        }
