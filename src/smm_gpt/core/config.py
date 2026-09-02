"""Typed application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by API, MCP and background workers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SMM_",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
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
    auth_enabled: bool = False
    knowledge_worker_enabled: bool = False
    ai_provider: Literal["disabled", "openai"] = "disabled"
    ai_model: str = ""
    ai_api_key: SecretStr = SecretStr("")
    ai_allowed_workspaces: tuple[UUID, ...] = ()
    ai_daily_run_limit: int = 5

    @model_validator(mode="after")
    def validate_ai(self) -> "Settings":
        if not 1 <= self.ai_daily_run_limit <= 100 or len(self.ai_model) > 120:
            raise ValueError("Invalid AI limits")
        if self.ai_provider != "disabled" and not (
            self.ai_model and self.ai_api_key.get_secret_value() and self.ai_allowed_workspaces
        ):
            raise ValueError("AI requires an explicit model, server key and workspace allowlist")
        return self

    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    mcp_issuer_url: str = ""
    mcp_client_id: str = ""
    mcp_resource_url: str = "http://127.0.0.1:8080/mcp/"
    session_idle_seconds: int = 1800
    session_absolute_seconds: int = 28800

    @model_validator(mode="after")
    def validate_auth(self) -> "Settings":
        if not self.auth_enabled:
            return self
        for value in (
            self.web_origin,
            self.oidc_issuer_url,
            self.mcp_issuer_url,
            self.mcp_resource_url,
        ):
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Authentication requires canonical HTTPS URLs")
        if urlsplit(self.web_origin).path not in ("", "/") or self.web_origin.endswith("/"):
            raise ValueError("Web origin must not contain a path or trailing slash")
        if self.mcp_resource_url != self.web_origin + "/mcp/":
            raise ValueError("MCP resource must match the same-origin canonical endpoint")
        if not all(
            (self.oidc_client_id, self.oidc_client_secret.get_secret_value(), self.mcp_client_id)
        ):
            raise ValueError("OIDC and predefined MCP clients are required")
        if not 60 <= self.session_idle_seconds <= self.session_absolute_seconds <= 28800:
            raise ValueError("Invalid session lifetime")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable settings object per process."""

    return Settings()
