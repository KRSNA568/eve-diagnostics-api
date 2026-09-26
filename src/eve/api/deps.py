from collections.abc import AsyncIterator
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eve.core.config import Settings
from eve.core.queue import TaskQueue


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services own the transaction (commit/rollback)."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def get_redis(request: Request) -> ArqRedis:
    redis: ArqRedis = request.app.state.redis
    return redis


def get_task_queue(request: Request) -> TaskQueue:
    queue: TaskQueue = request.app.state.task_queue
    return queue


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[ArqRedis, Depends(get_redis)]
TaskQueueDep = Annotated[TaskQueue, Depends(get_task_queue)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
