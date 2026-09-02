"""Typed application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by API, MCP and background workers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SMM_",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    timezone: str = "Europe/Moscow"
    api_bind: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "http://127.0.0.1:8080"
    database_url: SecretStr = SecretStr("postgresql+asyncpg://smm_gpt@127.0.0.1:5432/smm_gpt")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    dependency_timeout_seconds: float = 2.0
    media_root: str = ".data/media"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable settings object per process."""

    return Settings()
