from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from uuid_utils.compat import uuid7

from eve.core.config import Settings
from eve.core.errors import AuthenticationError
from eve.core.security import (
    TokenType,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from tests.factories import make_settings


@pytest.fixture(scope="module")
def settings() -> Settings:
    return make_settings(access_token_ttl_minutes=15, refresh_token_ttl_days=7)


def _encode(settings: Settings, drop: tuple[str, ...] = (), **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(uuid7()),
        "type": "access",
        "jti": "jti-1",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    } | overrides
    for claim in drop:
        claims.pop(claim)
    return jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), algorithm="HS256")


def test_password_hash_verifies_only_the_original_password() -> None:
    hashed = hash_password("Password123")

    assert hashed.startswith("$argon2id$")
    assert verify_password("Password123", hashed) == (True, None)
    assert verify_password("Password124", hashed)[0] is False


@pytest.mark.parametrize(
    ("token_type", "expected_ttl"),
    [(TokenType.ACCESS, 15 * 60), (TokenType.REFRESH, 7 * 24 * 3600)],
)
def test_token_round_trip(settings: Settings, token_type: TokenType, expected_ttl: int) -> None:
    subject: UUID = uuid7()

    issued = create_token(subject, token_type, settings)
    claims = decode_token(issued.token, token_type, settings)

    assert claims.subject == subject
    assert claims.token_type is token_type
    assert issued.expires_in == expected_ttl


def test_each_token_gets_a_unique_id(settings: Settings) -> None:
    subject: UUID = uuid7()

    first = decode_token(
        create_token(subject, TokenType.ACCESS, settings).token, TokenType.ACCESS, settings
    )
    second = decode_token(
        create_token(subject, TokenType.ACCESS, settings).token, TokenType.ACCESS, settings
    )

    assert first.token_id != second.token_id


def test_refresh_token_is_rejected_where_access_token_is_expected(settings: Settings) -> None:
    refresh = create_token(uuid7(), TokenType.REFRESH, settings).token

    with pytest.raises(AuthenticationError, match="Invalid token type"):
        decode_token(refresh, TokenType.ACCESS, settings)


def test_expired_token_is_rejected_with_a_specific_code(settings: Settings) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = _encode(settings, iat=past, exp=past + timedelta(minutes=1))

    with pytest.raises(AuthenticationError) as exc_info:
        decode_token(token, TokenType.ACCESS, settings)
    assert exc_info.value.code == "TOKEN_EXPIRED"


@pytest.mark.parametrize(
    "token_kwargs",
    [
        pytest.param({"aud": "someone-else"}, id="wrong-audience"),
        pytest.param({"iss": "someone-else"}, id="wrong-issuer"),
        pytest.param({"sub": "not-a-uuid"}, id="malformed-subject"),
        pytest.param({"drop": ("jti",)}, id="missing-token-id"),
        pytest.param({"drop": ("type",)}, id="missing-token-type"),
    ],
)
def test_tokens_with_invalid_claims_are_rejected(
    settings: Settings, token_kwargs: dict[str, Any]
) -> None:
    token = _encode(settings, **token_kwargs)

    with pytest.raises(AuthenticationError) as exc_info:
        decode_token(token, TokenType.ACCESS, settings)
    assert exc_info.value.code == "INVALID_TOKEN"


def test_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    forged = jwt.encode(
        {"sub": str(uuid7()), "type": "access"}, "another-key-" * 4, algorithm="HS256"
    )

    with pytest.raises(AuthenticationError):
        decode_token(forged, TokenType.ACCESS, settings)


def test_unsigned_alg_none_token_is_rejected(settings: Settings) -> None:
    unsigned = jwt.encode(
        {
            "sub": str(uuid7()),
            "type": "access",
            "jti": "x",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        key=None,
        algorithm="none",
    )

    with pytest.raises(AuthenticationError):
        decode_token(unsigned, TokenType.ACCESS, settings)
