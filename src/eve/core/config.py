from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "EVE Diagnostics API"
    environment: Literal["local", "test", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Logging --------------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # json for containers / log shippers, console for humans in a terminal.
    log_format: Literal["json", "console"] = "json"

    # --- Database -------------------------------------------------------------------------
    # On Supabase, DATABASE_URL is the Supavisor pooler URL used by the running app and
    # MIGRATIONS_DATABASE_URL is the session-mode / direct URL used by Alembic.
    database_url: str = Field(repr=False)
    migrations_database_url: str | None = Field(default=None, repr=False)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_echo: bool = False
    # Supavisor in transaction mode (port 6543) cannot use server-side prepared statements.
    db_disable_prepared_statements: bool = True

    @field_validator("database_url", "migrations_database_url")
    @classmethod
    def _use_psycopg_driver(cls, url: str | None) -> str | None:
        """Accept plain `postgres://` / `postgresql://` URLs exactly as Supabase displays them."""
        if url is None:
            return None
        for scheme in ("postgres://", "postgresql://"):
            if url.startswith(scheme):
                return "postgresql+psycopg://" + url.removeprefix(scheme)
        return url

    @property
    def migrations_url(self) -> str:
        return self.migrations_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
