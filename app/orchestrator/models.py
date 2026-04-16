from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any

class TaskType(str, Enum):
    CHAT = "chat"
    CODING = "coding"
    ARCHITECTURE = "architecture"
    RESEARCH = "research"

class PlanStepType(str, Enum):
    LLM_CHAT = "llm_chat"
    TOOL_CALL = "tool_call"
    FINALIZE = "finalize"

class ExecutionContext(BaseModel):
    request_id: str
    session_id: str
    task_type: TaskType = TaskType.CHAT
    metadata: dict[str, Any] = Field(default_factory=dict)

class SessionMessage(BaseModel):
    role: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SessionState(BaseModel):
    session_id: str
    messages: list[SessionMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class PlanStep(BaseModel):
    step_type: PlanStepType
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)

class ExecutionPlan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)

class OrchestratorResult(BaseModel):
    request_id: str
    session_id: str
    task_type: TaskType
    answer: str