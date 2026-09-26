"""Cache-aside on Redis with O(1) invalidation of a whole namespace.

Keys look like `{namespace}:v{version}:{digest}`. Writers bump the namespace version
(`INCR {namespace}:version`), which orphans every existing entry at once - no key scanning.
It also closes the classic race where a slow reader caches data it loaded just before a
write: that entry lands under the old version, which nothing reads any more. Orphans expire
through their TTL.

The cache is an optimisation, never a dependency: any Redis failure degrades to reading
from the database (status BYPASS) instead of failing the request.
"""

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

CacheStatus = Literal["HIT", "MISS", "BYPASS"]
_REDIS_FAILURES = (RedisError, OSError)


@dataclass(frozen=True, slots=True)
class Cached[T]:
    value: T
    status: CacheStatus


class VersionedCache:
    def __init__(self, redis: Redis, *, namespace: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._namespace = namespace
        self._ttl = ttl_seconds
        self._version_key = f"{namespace}:version"

    def _key(self, version: int, parts: Sequence[object]) -> str:
        digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]
        return f"{self._namespace}:v{version}:{digest}"

    async def get_or_load[T](
        self, parts: Sequence[object], adapter: TypeAdapter[T], load: Callable[[], Awaitable[T]]
    ) -> Cached[T]:
        try:
            version = int(await self._redis.get(self._version_key) or 0)
            key = self._key(version, parts)
            raw = await self._redis.get(key)
        except _REDIS_FAILURES as exc:
            logger.warning("cache.unavailable", namespace=self._namespace, error=str(exc))
            return Cached(await load(), "BYPASS")

        if raw is not None:
            try:
                return Cached(adapter.validate_json(raw), "HIT")
            except ValidationError:
                # e.g. written by an older release with a different schema: reload.
                logger.warning("cache.corrupt_entry", key=key)

        value = await load()
        try:
            await self._redis.set(key, adapter.dump_json(value), ex=self._ttl)
        except _REDIS_FAILURES as exc:
            logger.warning("cache.write_failed", namespace=self._namespace, error=str(exc))
        return Cached(value, "MISS")

    async def invalidate(self) -> None:
        try:
            await self._redis.incr(self._version_key)
        except _REDIS_FAILURES as exc:
            # Stale reads are now possible until entries expire (bounded by the TTL).
            logger.error("cache.invalidation_failed", namespace=self._namespace, error=str(exc))
