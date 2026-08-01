from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Local AI Agent"
    app_env: str = "dev"
    api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    rate_limit_requests_per_minute: int = 120
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_file_name: str = "app.log"
    log_to_file: bool = True
    log_json: bool = True
    telemetry_enabled: bool = False
    telemetry_exporter_otlp_endpoint: str | None = None
    telemetry_service_name: str = "ai-agent-worker"

    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = "google/gemma-4-e4b"
    llm_max_output_tokens: int = Field(default=1_024, ge=16, le=32_768)

    enable_memory: bool = False
    memory_backend: str = "noop"
    memory_file_path: str = "data/memory/interactions.jsonl"
    memory_sqlite_path: str | None = None
    memory_recall_limit: int = 5
    memory_max_recall_limit: int = 20
    memory_ttl_days: int = Field(default=90, ge=1, le=3_650)

    session_max_sessions: int = 200
    session_max_messages: int = 50
    state_db_path: str | None = None
    task_worktree_root: str | None = None
    run_event_poll_interval_seconds: float = Field(default=0.1, gt=0, le=5)

    agent_max_steps: int = Field(default=6, ge=1, le=50)
    agent_max_tool_calls: int = Field(default=10, ge=1, le=100)
    agent_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    approval_ttl_seconds: float = Field(default=300.0, gt=0, le=3600)
    approval_max_pending: int = Field(default=200, ge=1, le=10_000)

    tool_workspace_root: str = "."
    tool_allowed_commands: str = "git,python,pytest,uv,ruff"
    tool_command_timeout_seconds: float = 30.0
    tool_max_output_chars: int = 20_000
    tool_max_file_bytes: int = 200_000

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="after")
    def validate_telemetry(self) -> Settings:
        if not self.telemetry_enabled:
            return self
        if not self.telemetry_exporter_otlp_endpoint:
            raise ValueError("OTLP endpoint is required when telemetry is enabled")

        parsed = urlparse(self.telemetry_exporter_otlp_endpoint)
        local_development = self.app_env in {"dev", "test"} and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "otel-collector",
        }
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and local_development
        ):
            raise ValueError("OTLP endpoint must use HTTPS")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("OTLP endpoint must contain only the collector origin")
        return self

    def resolve_project_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return ROOT_DIR / path

    def resolve_state_db_path(self) -> Path:
        if self.state_db_path and self.state_db_path.strip():
            return self.resolve_project_path(self.state_db_path)

        if sys.platform == "win32":
            base = Path(
                os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or Path.home()
            )
            return base / "AI Agent" / "worker.sqlite3"
        if sys.platform == "darwin":
            return (
                Path.home()
                / "Library"
                / "Application Support"
                / "AI Agent"
                / "worker.sqlite3"
            )

        xdg_state_home = os.environ.get("XDG_STATE_HOME")
        base = (
            Path(xdg_state_home) if xdg_state_home else Path.home() / ".local" / "state"
        )
        return base / "ai-agent" / "worker.sqlite3"

    def resolve_task_worktree_root(self) -> Path:
        if self.task_worktree_root and self.task_worktree_root.strip():
            return self.resolve_project_path(self.task_worktree_root)
        return self.resolve_state_db_path().parent / "worktrees"

    def resolve_memory_db_path(self) -> Path:
        if self.memory_sqlite_path and self.memory_sqlite_path.strip():
            return self.resolve_project_path(self.memory_sqlite_path)
        return self.resolve_state_db_path().parent / "knowledge.sqlite3"

    def allowed_tool_commands(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.tool_allowed_commands.split(",")
            if item.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
