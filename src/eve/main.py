from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

import structlog
from arq.connections import ArqRedis
from fastapi import FastAPI

from eve.api.rate_limit import create_rate_limiter
from eve.api.v1 import api_router
from eve.core.config import Settings, get_settings
from eve.core.db import create_engine, create_session_factory
from eve.core.errors import register_exception_handlers
from eve.core.logging import configure_logging
from eve.core.middleware import RequestContextMiddleware
from eve.core.queue import ArqTaskQueue

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory. Run with `uvicorn eve.main:create_app --factory`."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        # Connects lazily: the API still starts (and stays useful) if Redis is briefly down.
        redis = ArqRedis.from_url(settings.redis_url)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.task_queue = ArqTaskQueue(redis)
        app.state.rate_limiter = create_rate_limiter(redis)
        logger.info("app.startup", environment=settings.environment)
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            logger.info("app.shutdown")

    app = FastAPI(
        title=settings.app_name,
        version=version("eve"),
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
