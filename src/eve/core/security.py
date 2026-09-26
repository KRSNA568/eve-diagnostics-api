"""Password hashing and JWT handling. Pure functions: no HTTP or database concerns."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from uuid_utils.compat import uuid7

from eve.core.config import Settings
from eve.core.errors import AuthenticationError

# Argon2id with pwdlib's recommended parameters.
_password_hash = PasswordHash.recommended()

# Verified against when the account does not exist, so a failed login takes the same time
# whether or not the email is registered (prevents user enumeration by timing).
_DUMMY_PASSWORD_HASH = _password_hash.hash("dummy-password-for-constant-time-login")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Return (is_valid, upgraded_hash). `upgraded_hash` is set when the stored hash uses
    outdated parameters and should be replaced."""
    return _password_hash.verify_and_update(password, password_hash)


def burn_password_check(password: str) -> None:
    _password_hash.verify(password, _DUMMY_PASSWORD_HASH)


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_in: int  # seconds


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: UUID
    token_type: TokenType
    token_id: str


def create_token(subject: UUID, token_type: TokenType, settings: Settings) -> IssuedToken:
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type is TokenType.ACCESS
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = datetime.now(UTC)
    claims = {
        "sub": str(subject),
        "type": token_type.value,
        "jti": uuid7().hex,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    token = jwt.encode(
        claims, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return IssuedToken(token=token, expires_in=int(ttl.total_seconds()))


def decode_token(token: str, expected_type: TokenType, settings: Settings) -> TokenClaims:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            # Pin the algorithm: never trust the token's own header (blocks `alg: none`).
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "type", "jti", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token", code="INVALID_TOKEN") from exc

    if claims["type"] != expected_type.value:
        raise AuthenticationError("Invalid token type", code="INVALID_TOKEN")
    try:
        subject = UUID(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("Invalid token", code="INVALID_TOKEN") from exc

    return TokenClaims(subject=subject, token_type=expected_type, token_id=claims["jti"])
