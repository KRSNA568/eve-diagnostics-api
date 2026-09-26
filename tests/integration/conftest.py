from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from decimal import Decimal

import pytest
from arq.connections import ArqRedis
from arq.worker import Worker
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from eve.auth.models import User
from eve.core.config import Settings, get_settings
from eve.core.db import create_session_factory
from eve.main import create_app
from eve.models import Base
from eve.worker.registry import FUNCTIONS
from tests.factories import (
    PERSISTED_FACTORIES,
    AuthHeaders,
    CentreFactory,
    DiagnosticTestFactory,
    Offering,
    OfferingFactory,
    UserFactory,
)


@pytest.fixture
async def app(settings: Settings, db_engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session for arranging data and asserting on it directly; factories persist via it."""
    async with create_session_factory(db_engine)() as session:
        for factory in PERSISTED_FACTORIES:
            factory.__async_session__ = session
        yield session


@pytest.fixture
async def user(db_session: AsyncSession) -> User:
    return await UserFactory.create_async()


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await UserFactory.create_async(is_admin=True)


@pytest.fixture
def auth_headers(settings: Settings) -> AuthHeaders:
    return AuthHeaders(settings)


@pytest.fixture
async def offering(db_session: AsyncSession) -> Offering:
    """CBC at a Mumbai centre for 349.00 INR."""
    centre = await CentreFactory.create_async(name="CareLab Andheri", city="Mumbai")
    test = await DiagnosticTestFactory.create_async(code="CBC", name="Complete Blood Count")
    row = await OfferingFactory.create_async(
        centre_id=centre.id, test_id=test.id, price=Decimal("349.00")
    )
    return Offering(centre, test, row)


@pytest.fixture
def app_env(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, migrated_database: None
) -> Iterator[None]:
    """Point code that reads settings from the environment (CLI, worker startup) at the
    test database and Redis."""
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    monkeypatch.setenv("JWT_SECRET_KEY", settings.jwt_secret_key.get_secret_value())
    monkeypatch.setenv("WEBHOOK_SECRET", settings.webhook_secret.get_secret_value())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


RunWorker = Callable[[], Awaitable[None]]


@pytest.fixture
def run_worker(settings: Settings, db_engine: AsyncEngine) -> RunWorker:
    """Run a real ARQ worker in-process until the queue is empty (burst mode)."""

    async def run() -> None:
        worker = Worker(
            functions=FUNCTIONS,
            redis_pool=ArqRedis.from_url(settings.redis_url),
            burst=True,
            poll_delay=0.01,
            handle_signals=False,
            ctx={"settings": settings, "session_factory": create_session_factory(db_engine)},
        )
        try:
            await worker.main()
        finally:
            await worker.close()

    return run


@pytest.fixture(autouse=True)
async def _clean_redis(settings: Settings) -> AsyncIterator[None]:
    yield
    redis = ArqRedis.from_url(settings.redis_url)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


@pytest.fixture(autouse=True)
async def _clean_database(db_engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if tables:
        async with db_engine.begin() as conn:
            # Table names come from our own metadata, never from user input.
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
