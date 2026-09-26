from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from eve.auth.models import User
from eve.core.config import Settings
from eve.main import create_app
from tests.factories import AuthHeaders, UserFactory

LOGIN = "/api/v1/auth/login/"
SIGNUP = "/api/v1/auth/signup/"
PAYMENTS = "/api/v1/payments/"
UNKNOWN_BOOKING = {"booking_id": "01a0dcae-0000-7000-8000-000000000000"}

ClientFactory = Callable[..., AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def make_client(settings: Settings, db_engine: object) -> ClientFactory:
    """A client for an app built with overridden settings, optionally from a given IP."""

    @asynccontextmanager
    async def factory(ip: str = "203.0.113.10", **overrides: Any) -> AsyncIterator[AsyncClient]:
        app = create_app(settings.model_copy(update=overrides))
        transport = ASGITransport(app=app, client=(ip, 50000))
        async with (
            LifespanManager(app),
            AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            yield client

    return factory


async def failed_logins(client: AsyncClient, times: int) -> list[Response]:
    body = {"email": "nobody@example.com", "password": "Wrong12345"}
    return [await client.post(LOGIN, json=body) for _ in range(times)]


async def test_login_is_limited_per_client_ip(make_client: ClientFactory) -> None:
    async with make_client(rate_limit_login="3/minute") as client:
        responses = await failed_logins(client, 4)

    assert [r.status_code for r in responses] == [401, 401, 401, 429]
    blocked = responses[-1]
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["Retry-After"]) > 0
    assert blocked.headers["X-RateLimit-Limit"] == "3"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


async def test_allowed_responses_report_the_remaining_budget(make_client: ClientFactory) -> None:
    async with make_client(rate_limit_login="3/minute") as client:
        responses = await failed_logins(client, 2)

    assert [r.headers["X-RateLimit-Remaining"] for r in responses] == ["2", "1"]


async def test_each_client_ip_has_its_own_budget(make_client: ClientFactory) -> None:
    async with make_client(ip="203.0.113.10", rate_limit_login="2/minute") as attacker:
        await failed_logins(attacker, 3)
    async with make_client(ip="198.51.100.7", rate_limit_login="2/minute") as someone_else:
        [response] = await failed_logins(someone_else, 1)

    assert response.status_code == 401  # not 429


async def test_endpoints_have_independent_budgets(make_client: ClientFactory) -> None:
    async with make_client(rate_limit_login="1/minute", rate_limit_signup="1/minute") as client:
        await failed_logins(client, 2)
        signup = await client.post(
            SIGNUP,
            json={"email": "new@example.com", "password": "Password123", "full_name": "New"},
        )

    assert signup.status_code == 201


async def test_payments_are_limited_per_user(
    make_client: ClientFactory, user: User, auth_headers: AuthHeaders
) -> None:
    other = await UserFactory.create_async()
    async with make_client(rate_limit_payments="2/minute") as client:
        mine = [
            await client.post(PAYMENTS, json=UNKNOWN_BOOKING, headers=auth_headers(user))
            for _ in range(3)
        ]
        theirs = await client.post(PAYMENTS, json=UNKNOWN_BOOKING, headers=auth_headers(other))

    assert [r.status_code for r in mine] == [404, 404, 429]
    assert theirs.status_code == 404  # same IP, different user: unaffected


async def test_limits_can_be_switched_off(make_client: ClientFactory) -> None:
    async with make_client(rate_limit_enabled=False, rate_limit_login="1/minute") as client:
        responses = await failed_logins(client, 3)

    assert {r.status_code for r in responses} == {401}
    assert "X-RateLimit-Limit" not in responses[0].headers


async def test_limiter_fails_open_when_redis_is_down(make_client: ClientFactory) -> None:
    async with make_client(
        redis_url="redis://127.0.0.1:1/0", rate_limit_login="1/minute"
    ) as client:
        responses = await failed_logins(client, 3)

    assert {r.status_code for r in responses} == {401}  # never 429, never 500


def test_malformed_limits_are_rejected_at_startup() -> None:
    from pydantic import ValidationError

    from tests.factories import make_settings

    with pytest.raises(ValidationError, match="rate_limit_login"):
        make_settings(rate_limit_login="lots per minute")
