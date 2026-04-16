from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Local AI Agent"
    app_env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_file_name: str = "app.log"
    log_to_file: bool = True

    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = "google/gemma-4-e4b"

    enable_memory: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
