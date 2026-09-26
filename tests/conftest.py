"""Session-wide test infrastructure.

Tests never read `.env`, so they can never reach the Supabase database. By default a
throwaway Postgres container is started once per session; set TEST_DATABASE_URL to use an
existing server instead (e.g. a CI service container).
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer

from eve.core.config import Settings
from eve.core.db import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = "postgres:16-alpine"
TEST_JWT_SECRET = "test-only-jwt-secret-that-is-at-least-32-bytes-long"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        jwt_secret_key=SecretStr(TEST_JWT_SECRET),
    )


@pytest.fixture(scope="session")
def migrated_database(settings: Settings) -> None:
    """Build the schema with the real migrations, which also tests the migrations."""
    config = Config(toml_file=str(PROJECT_ROOT / "pyproject.toml"))
    config.attributes["database_url"] = settings.migrations_url
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
async def db_engine(settings: Settings, migrated_database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    yield engine
    await engine.dispose()
