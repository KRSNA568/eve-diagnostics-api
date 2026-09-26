"""Session-wide test infrastructure.

Tests never read `.env`, so they can never reach the Supabase database. By default
throwaway Postgres and Redis containers are started once per session; set
TEST_DATABASE_URL / TEST_REDIS_URL to use existing servers instead (e.g. CI services, or a
local Redis on a dedicated database number such as redis://localhost:6379/15).
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
from testcontainers.community.redis import RedisContainer

from eve.core.config import Settings
from eve.core.db import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = "postgres:16-alpine"
REDIS_IMAGE = "redis:7-alpine"
TEST_JWT_SECRET = "test-only-jwt-secret-that-is-at-least-32-bytes-long"
TEST_WEBHOOK_SECRET = "test-only-webhook-secret-at-least-32-bytes-long"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    if url := os.environ.get("TEST_REDIS_URL"):
        yield url
        return
    with RedisContainer(REDIS_IMAGE) as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def settings(database_url: str, redis_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret_key=SecretStr(TEST_JWT_SECRET),
        webhook_secret=SecretStr(TEST_WEBHOOK_SECRET),
        # Fast, bounded retries so worker tests finish in milliseconds.
        webhook_max_attempts=3,
        webhook_retry_base_seconds=0.01,
        webhook_retry_max_seconds=0.05,
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
