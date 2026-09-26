"""Per-route rate limiting on Redis.

The algorithm comes from the maintained `limits` library (sliding-window counter: smoother
than a fixed window, far cheaper than a true sliding log). This module only adapts it to
FastAPI: a dependency per route, standard `X-RateLimit-*` / `Retry-After` headers, and the
shared error envelope.

Like the cache, the limiter fails open: if Redis is unavailable requests are allowed and a
warning is logged - an outage of the limiter must not become an outage of the API.
"""

import math
import time
from collections.abc import Awaitable, Callable
from functools import lru_cache

import structlog
from fastapi import Request
from limits import RateLimitItem, parse
from limits.aio.storage import RedisStorage
from limits.aio.strategies import SlidingWindowCounterRateLimiter
from limits.errors import StorageError
from redis.asyncio import Redis

from eve.auth.dependencies import CurrentUser
from eve.core.config import Settings
from eve.core.errors import AppError
from eve.core.middleware import EXTRA_HEADERS_STATE

logger = structlog.get_logger(__name__)


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"


def create_rate_limiter(redis: Redis) -> SlidingWindowCounterRateLimiter:
    """Build a limiter that shares the application's Redis connection pool (one pool, one
    lifecycle: closed with the app)."""
    storage = RedisStorage(
        "async+redis://",
        implementation="redispy",
        wrap_exceptions=True,
        key_prefix="ratelimit",
        connection_pool=redis.connection_pool,  # type: ignore[arg-type]
    )
    return SlidingWindowCounterRateLimiter(storage)


async def _enforce(request: Request, item: RateLimitItem, scope: str, identity: str) -> None:
    limiter: SlidingWindowCounterRateLimiter = request.app.state.rate_limiter
    try:
        allowed = await limiter.hit(item, scope, identity)
        stats = await limiter.get_window_stats(item, scope, identity)
    except StorageError as exc:
        logger.warning("rate_limit.unavailable", scope=scope, error=str(exc))
        return

    retry_after = max(1, math.ceil(stats.reset_time - time.time()))
    headers = {
        "X-RateLimit-Limit": str(item.amount),
        "X-RateLimit-Remaining": str(stats.remaining),
        "X-RateLimit-Reset": str(math.ceil(stats.reset_time)),
    }
    if not allowed:
        logger.warning("rate_limit.exceeded", scope=scope, limit=str(item))
        raise RateLimitedError(
            "Too many requests, please retry later",
            details={"limit": str(item), "retry_after_seconds": retry_after},
            headers=headers | {"Retry-After": str(retry_after)},
        )
    # Attached by the request middleware to the final response, error responses included.
    setattr(request.state, EXTRA_HEADERS_STATE, headers)


def _client_ip(request: Request) -> str:
    # Behind a proxy, run uvicorn with --proxy-headers so this is the real client address;
    # never trust X-Forwarded-For directly (any client can set it).
    return request.client.host if request.client else "unknown"


@lru_cache
def _parse(limit: str) -> RateLimitItem:
    return parse(limit)


def _configured_limit(request: Request, setting: str) -> RateLimitItem | None:
    settings: Settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return None
    return _parse(getattr(settings, setting))


def limit_by_ip(setting: str, *, scope: str) -> Callable[..., Awaitable[None]]:
    """For anonymous endpoints (login, signup): one budget per client IP. `setting` names the
    Settings field holding the limit, e.g. "5/minute"."""

    async def dependency(request: Request) -> None:
        if item := _configured_limit(request, setting):
            await _enforce(request, item, scope, _client_ip(request))

    return dependency


def limit_by_user(setting: str, *, scope: str) -> Callable[..., Awaitable[None]]:
    """For authenticated endpoints: one budget per user, wherever they connect from."""

    async def dependency(request: Request, user: CurrentUser) -> None:
        if item := _configured_limit(request, setting):
            await _enforce(request, item, scope, str(user.id))

    return dependency
