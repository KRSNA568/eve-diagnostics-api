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

API_DESCRIPTION = """
Book diagnostic tests at partner centres and pay through a simulated payment gateway.

**Typical flow:** `POST /auth/signup/` → `GET /centres/` → `POST /bookings/` (PENDING)
→ `POST /payments/` (CONFIRMED or FAILED). The payment provider reports results
asynchronously to `POST /payments/webhook/`, which is signed and idempotent.

**Authentication:** click *Authorize* and paste the `access_token` from signup or login.

**Errors** always use one envelope:
`{"error": {"code", "message", "details"}, "request_id"}` - quote the `request_id`
(also in the `X-Request-ID` header) when reporting a problem.
"""

OPENAPI_TAGS = [
    {"name": "auth", "description": "Signup, login and JWT access/refresh tokens."},
    {
        "name": "catalog",
        "description": "Diagnostic centres, tests and prices. Public reads (cached); "
        "writes require an admin.",
    },
    {"name": "bookings", "description": "Book a test; a booking stays PENDING until paid."},
    {
        "name": "payments",
        "description": "Simulated payments, the provider webhook and its admin operations.",
    },
    {"name": "health", "description": "Liveness and readiness probes."},
]


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
        version=version("eve-diagnostics"),
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        # Keep the Bearer token entered via "Authorize" across page reloads.
        swagger_ui_parameters={"persistAuthorization": True},
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
