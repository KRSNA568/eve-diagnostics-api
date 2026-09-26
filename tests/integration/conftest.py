from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from eve.auth.models import User
from eve.core.config import Settings
from eve.core.db import create_session_factory
from eve.main import create_app
from eve.models import Base
from tests.factories import AuthHeaders, UserFactory


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
        UserFactory.__async_session__ = session
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


@pytest.fixture(autouse=True)
async def _clean_database(db_engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if tables:
        async with db_engine.begin() as conn:
            # Table names come from our own metadata, never from user input.
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
