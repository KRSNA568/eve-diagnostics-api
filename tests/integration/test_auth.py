from typing import Any

import pytest
from httpx import AsyncClient
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.core.security import TokenType
from tests.factories import TEST_PASSWORD, AuthHeaders, UserFactory

SIGNUP = "/api/v1/auth/signup/"
LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
ME = "/api/v1/auth/me/"

SIGNUP_PAYLOAD: dict[str, Any] = {
    "email": "Asha.Verma@Example.com",
    "password": "Password123",
    "full_name": "Asha Verma",
    "phone": "+919812345678",
}


# --------------------------------------------------------------------------- signup


async def test_signup_creates_account_and_returns_working_tokens(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(SIGNUP, json=SIGNUP_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "asha.verma@example.com"
    assert body["user"]["is_admin"] is False
    assert body["user"]["created_at"].endswith("Z"), "timestamps must be serialised in UTC"
    assert "password_hash" not in body["user"]
    assert SIGNUP_PAYLOAD["password"] not in response.text
    assert body["tokens"]["token_type"] == "bearer"

    me = await client.get(ME, headers={"Authorization": f"Bearer {body['tokens']['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]

    stored = (await db_session.execute(select(User))).scalar_one()
    assert stored.password_hash.startswith("$argon2id$")


async def test_signup_rejects_duplicate_email_regardless_of_case(client: AsyncClient) -> None:
    await client.post(SIGNUP, json=SIGNUP_PAYLOAD)

    response = await client.post(SIGNUP, json=SIGNUP_PAYLOAD | {"email": "ASHA.VERMA@example.COM"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


async def test_signup_cannot_self_assign_admin(client: AsyncClient) -> None:
    response = await client.post(SIGNUP, json=SIGNUP_PAYLOAD | {"is_admin": True})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["errors"][0]["field"] == "is_admin"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("password", "short1"),
        ("password", "lettersonly"),
        ("full_name", ""),
    ],
)
async def test_signup_validates_input(client: AsyncClient, field: str, value: str) -> None:
    response = await client.post(SIGNUP, json=SIGNUP_PAYLOAD | {field: value})

    assert response.status_code == 422
    assert {e["field"] for e in response.json()["error"]["details"]["errors"]} == {field}


# --------------------------------------------------------------------------- login


async def test_login_returns_access_and_refresh_tokens(client: AsyncClient, user: User) -> None:
    response = await client.post(LOGIN, json={"email": user.email, "password": TEST_PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 15 * 60
    assert body["refresh_expires_in"] == 7 * 24 * 3600
    me = await client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == user.email


async def test_login_email_is_case_insensitive(client: AsyncClient, user: User) -> None:
    response = await client.post(
        LOGIN, json={"email": user.email.upper(), "password": TEST_PASSWORD}
    )

    assert response.status_code == 200


async def test_login_failure_does_not_reveal_whether_the_account_exists(
    client: AsyncClient, user: User
) -> None:
    wrong_password = await client.post(LOGIN, json={"email": user.email, "password": "Nope12345"})
    unknown_email = await client.post(
        LOGIN, json={"email": "ghost@example.com", "password": TEST_PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"] == unknown_email.json()["error"]
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_is_refused_for_disabled_accounts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    disabled = await UserFactory.create_async(is_active=False)

    response = await client.post(LOGIN, json={"email": disabled.email, "password": TEST_PASSWORD})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"


async def test_login_upgrades_password_hashes_with_outdated_parameters(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    weak_hash = Argon2Hasher(time_cost=1, memory_cost=8 * 1024).hash(TEST_PASSWORD)
    legacy = await UserFactory.create_async(password_hash=weak_hash)

    response = await client.post(LOGIN, json={"email": legacy.email, "password": TEST_PASSWORD})

    assert response.status_code == 200
    await db_session.refresh(legacy)
    assert legacy.password_hash != weak_hash


# --------------------------------------------------------------------------- tokens


async def test_protected_route_requires_a_token(client: AsyncClient) -> None:
    response = await client.get(ME)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "Bearer "],
)
async def test_protected_route_rejects_malformed_credentials(
    client: AsyncClient, authorization: str
) -> None:
    response = await client.get(ME, headers={"Authorization": authorization})

    assert response.status_code == 401


async def test_refresh_token_cannot_be_used_as_access_token(
    client: AsyncClient, user: User, auth_headers: AuthHeaders
) -> None:
    response = await client.get(ME, headers=auth_headers(user, TokenType.REFRESH))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


async def test_token_of_deleted_user_is_rejected(
    client: AsyncClient, db_session: AsyncSession, user: User, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers(user)
    await db_session.delete(user)
    await db_session.commit()

    response = await client.get(ME, headers=headers)

    assert response.status_code == 401


async def test_token_of_disabled_user_is_rejected(
    client: AsyncClient, db_session: AsyncSession, user: User, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers(user)
    user.is_active = False
    await db_session.commit()

    response = await client.get(ME, headers=headers)

    assert response.status_code == 401


# --------------------------------------------------------------------------- refresh


async def test_refresh_issues_a_new_access_token(
    client: AsyncClient, user: User, auth_headers: AuthHeaders
) -> None:
    refresh_token = auth_headers(user, TokenType.REFRESH)["Authorization"].removeprefix("Bearer ")

    response = await client.post(REFRESH, json={"refresh_token": refresh_token})

    assert response.status_code == 200
    new_access = response.json()["access_token"]
    me = await client.get(ME, headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_refresh_rejects_access_tokens(
    client: AsyncClient, user: User, auth_headers: AuthHeaders
) -> None:
    access_token = auth_headers(user)["Authorization"].removeprefix("Bearer ")

    response = await client.post(REFRESH, json={"refresh_token": access_token})

    assert response.status_code == 401


async def test_refresh_is_refused_once_the_account_is_disabled(
    client: AsyncClient, db_session: AsyncSession, user: User, auth_headers: AuthHeaders
) -> None:
    refresh_token = auth_headers(user, TokenType.REFRESH)["Authorization"].removeprefix("Bearer ")
    user.is_active = False
    await db_session.commit()

    response = await client.post(REFRESH, json={"refresh_token": refresh_token})

    assert response.status_code == 401
