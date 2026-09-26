from typing import cast

import pytest
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from eve.core.cache import VersionedCache

INTS = TypeAdapter(list[int])


class StubRedis:
    """In-memory stand-in that can be told to fail specific commands."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.store: dict[str, bytes] = {}
        self.failing = failing or set()

    def _check(self, command: str) -> None:
        if command in self.failing:
            raise RedisConnectionError(f"{command} failed")

    async def get(self, key: str) -> bytes | None:
        self._check("get")
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self._check("set")
        self.store[key] = value

    async def incr(self, key: str) -> int:
        self._check("incr")
        value = int(self.store.get(key, b"0")) + 1
        self.store[key] = str(value).encode()
        return value


def make_cache(redis: StubRedis) -> VersionedCache:
    return VersionedCache(cast(Redis, redis), namespace="catalog", ttl_seconds=60)


async def load() -> list[int]:
    return [1, 2, 3]


async def test_miss_then_hit() -> None:
    cache = make_cache(StubRedis())

    first = await cache.get_or_load(("k",), INTS, load)
    second = await cache.get_or_load(("k",), INTS, load)

    assert (first.status, second.status) == ("MISS", "HIT")
    assert second.value == [1, 2, 3]


async def test_invalidation_orphans_existing_entries() -> None:
    cache = make_cache(StubRedis())
    await cache.get_or_load(("k",), INTS, load)

    await cache.invalidate()

    assert (await cache.get_or_load(("k",), INTS, load)).status == "MISS"


@pytest.mark.parametrize("failing", ["get", "set"])
async def test_redis_failures_degrade_to_the_database(failing: str) -> None:
    cache = make_cache(StubRedis({failing}))

    result = await cache.get_or_load(("k",), INTS, load)

    assert result.value == [1, 2, 3]
    assert result.status == ("BYPASS" if failing == "get" else "MISS")


async def test_failed_invalidation_does_not_fail_the_write() -> None:
    await make_cache(StubRedis({"incr"})).invalidate()  # logged, not raised
