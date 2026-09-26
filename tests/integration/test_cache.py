"""Catalog read cache (Redis, versioned keys)."""

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from arq.connections import ArqRedis
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.core.config import Settings
from eve.main import create_app
from tests.factories import AuthHeaders, CentreFactory, DiagnosticTestFactory, OfferingFactory

CENTRES = "/api/v1/centres/"


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[ArqRedis]:
    client = ArqRedis.from_url(settings.redis_url)
    yield client
    await client.aclose()


async def test_second_identical_read_is_served_from_the_cache(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await CentreFactory.create_batch_async(2)

    first = await client.get(CENTRES)
    second = await client.get(CENTRES)

    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")
    assert first.json() == second.json()


async def test_each_distinct_query_is_cached_separately(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await CentreFactory.create_async(city="Pune")

    assert (await client.get(CENTRES, params={"city": "Pune"})).headers["X-Cache"] == "MISS"
    assert (await client.get(CENTRES, params={"city": "pune"})).headers["X-Cache"] == "HIT"
    assert (await client.get(CENTRES, params={"page": 2})).headers["X-Cache"] == "MISS"


async def test_a_catalog_write_invalidates_every_cached_read(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    await CentreFactory.create_async(name="Alpha Labs")
    await client.get(CENTRES)
    await client.get("/api/v1/tests/")

    await client.post(
        CENTRES,
        json={"name": "Beta Labs", "address": "1 Road", "city": "Pune", "pincode": "411045"},
        headers=auth_headers(admin),
    )
    centres = await client.get(CENTRES)
    tests = await client.get("/api/v1/tests/")

    assert centres.headers["X-Cache"] == "MISS"
    assert [c["name"] for c in centres.json()["items"]] == ["Alpha Labs", "Beta Labs"]
    assert tests.headers["X-Cache"] == "MISS"  # whole namespace, not just the edited list


async def test_a_price_change_is_visible_immediately(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    centre = await CentreFactory.create_async()
    test = await DiagnosticTestFactory.create_async()
    await OfferingFactory.create_async(
        centre_id=centre.id, test_id=test.id, price=Decimal("499.00")
    )
    await client.get(f"{CENTRES}{centre.id}/")

    await client.patch(
        f"{CENTRES}{centre.id}/tests/{test.id}/",
        json={"price": "449.00"},
        headers=auth_headers(admin),
    )
    detail = await client.get(f"{CENTRES}{centre.id}/")

    assert detail.json()["offerings"][0]["price"] == "449.00"


async def test_a_deactivated_centre_disappears_immediately(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    centre = await CentreFactory.create_async()
    assert (await client.get(f"{CENTRES}{centre.id}/")).status_code == 200

    await client.delete(f"{CENTRES}{centre.id}/", headers=auth_headers(admin))

    assert (await client.get(f"{CENTRES}{centre.id}/")).status_code == 404
    assert (await client.get(CENTRES)).json()["total"] == 0


async def test_not_found_responses_are_not_cached(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    centre = await CentreFactory.create_async(is_active=False)
    assert (await client.get(f"{CENTRES}{centre.id}/")).status_code == 404

    await client.patch(
        f"{CENTRES}{centre.id}/", json={"is_active": True}, headers=auth_headers(admin)
    )

    assert (await client.get(f"{CENTRES}{centre.id}/")).status_code == 200


async def test_cache_entries_expire(
    client: AsyncClient, db_session: AsyncSession, redis: ArqRedis, settings: Settings
) -> None:
    await client.get(CENTRES)

    [key] = await redis.keys("catalog:v*")
    assert 0 < await redis.ttl(key) <= settings.catalog_cache_ttl_seconds


async def test_a_corrupt_entry_is_treated_as_a_miss(
    client: AsyncClient, db_session: AsyncSession, redis: ArqRedis
) -> None:
    await CentreFactory.create_async(name="Alpha Labs")
    await client.get(CENTRES)
    [key] = await redis.keys("catalog:v*")
    await redis.set(key, b"{not json")

    response = await client.get(CENTRES)

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["items"][0]["name"] == "Alpha Labs"


async def test_reads_still_work_when_redis_is_down(
    settings: Settings, db_engine: object, db_session: AsyncSession
) -> None:
    await CentreFactory.create_async(name="Alpha Labs")
    app = create_app(settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get(CENTRES)

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "BYPASS"
    assert [c["name"] for c in response.json()["items"]] == ["Alpha Labs"]
