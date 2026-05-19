import json
from typing import Any

from pydantic import BaseModel, Field, model_validator


MAX_MESSAGE_LENGTH = 12_000
MAX_SESSION_ID_LENGTH = 128
MAX_PROJECT_PATH_LENGTH = 500
MAX_METADATA_BYTES = 8_000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    session_id: str | None = Field(default=None, max_length=MAX_SESSION_ID_LENGTH)
    project_path: str | None = Field(default=None, max_length=MAX_PROJECT_PATH_LENGTH)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metadata_size(self) -> "ChatRequest":
        metadata_size = len(json.dumps(self.metadata, ensure_ascii=False).encode("utf-8"))
        if metadata_size > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be at most {MAX_METADATA_BYTES} bytes")
        return self


class ExecutionStep(BaseModel):
    name: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    route: str
    reply: str
    steps: list[ExecutionStep] = Field(default_factory=list)
