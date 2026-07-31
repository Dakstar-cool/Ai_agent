from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AutonomyMode(StrEnum):
    SAFE = "safe"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"


class PolicyBoundary(BaseModel):
    allowed_tools: frozenset[str] | None = Field(default=None, max_length=100)
    path_globs: tuple[str, ...] | None = Field(default=None, max_length=100)
    max_writes: int | None = Field(default=None, ge=0, le=1_000)
    max_commands: int | None = Field(default=None, ge=0, le=1_000)
    network_allowed: bool = False

    @field_validator("allowed_tools")
    @classmethod
    def normalize_tools(cls, value: frozenset[str] | None) -> frozenset[str] | None:
        if value is None:
            return None
        normalized = frozenset(item.strip() for item in value if item.strip())
        if any(len(item) > 64 for item in normalized):
            raise ValueError("tool names must be at most 64 characters")
        return normalized

    @field_validator("path_globs")
    @classmethod
    def validate_globs(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized = tuple(item.replace("\\", "/").strip() for item in value)
        if any(
            not item
            or len(item) > 200
            or "\x00" in item
            or item.startswith(("/", "../"))
            for item in normalized
        ):
            raise ValueError("path_globs must be workspace-relative patterns")
        return normalized


class RunPolicy(BaseModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    mode: AutonomyMode = AutonomyMode.SAFE
    ttl_seconds: int = Field(default=0, ge=0, le=86_400)
    issued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    allowed_tools: frozenset[str] = Field(default_factory=frozenset, max_length=100)
    path_globs: tuple[str, ...] = Field(default=(), max_length=100)
    max_writes: int = Field(default=0, ge=0, le=1_000)
    max_commands: int = Field(default=0, ge=0, le=1_000)
    network_allowed: bool = False

    @field_validator("issued_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("issued_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("allowed_tools")
    @classmethod
    def normalize_tools(cls, value: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(item.strip() for item in value if item.strip())
        if any(len(item) > 64 for item in normalized):
            raise ValueError("tool names must be at most 64 characters")
        return normalized

    @field_validator("path_globs")
    @classmethod
    def validate_globs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.replace("\\", "/").strip() for item in value)
        if any(
            not item
            or len(item) > 200
            or "\x00" in item
            or item.startswith(("/", "../"))
            for item in normalized
        ):
            raise ValueError("path_globs must be workspace-relative patterns")
        return normalized

    @model_validator(mode="after")
    def validate_grant(self) -> "RunPolicy":
        if self.mode is not AutonomyMode.SAFE and self.ttl_seconds == 0:
            raise ValueError("supervised and autonomous policies require a TTL")
        return self

    @classmethod
    def safe(cls) -> "RunPolicy":
        return cls()

    def activate(self, now: datetime | None = None) -> "RunPolicy":
        return self.model_copy(update={"issued_at": now or datetime.now(UTC)})

    def is_expired(self, now: datetime) -> bool:
        if self.mode is AutonomyMode.SAFE:
            return False
        return now >= self.issued_at + timedelta(seconds=self.ttl_seconds)


class PolicyUsage(BaseModel):
    writes: int = Field(default=0, ge=0)
    commands: int = Field(default=0, ge=0)

    def record(self, mutation_kind: str | None) -> None:
        if mutation_kind == "command":
            self.commands += 1
        elif mutation_kind is not None:
            self.writes += 1


class PolicyDecision(BaseModel):
    action: PolicyAction
    reason: str
    mode: AutonomyMode
    policy_layer: str
