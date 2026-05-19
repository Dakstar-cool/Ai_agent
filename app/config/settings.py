from functools import lru_cache
from pathlib import Path

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

    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = "google/gemma-4-e4b"

    enable_memory: bool = False
    memory_backend: str = "noop"
    memory_file_path: str = "data/memory/interactions.jsonl"
    memory_recall_limit: int = 5
    memory_max_recall_limit: int = 20

    session_max_sessions: int = 200
    session_max_messages: int = 50

    tool_workspace_root: str = "."
    tool_allowed_commands: str = "git,python,pytest,uv"
    tool_command_timeout_seconds: float = 30.0
    tool_max_output_chars: int = 20_000
    tool_max_file_bytes: int = 200_000

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    def resolve_project_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return ROOT_DIR / path

    def allowed_tool_commands(self) -> set[str]:
        return {item.strip().lower() for item in self.tool_allowed_commands.split(",") if item.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
