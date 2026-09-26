"""Test data factories (polyfactory).

Each factory builds valid model instances with sensible defaults; tests override only the
fields that matter to them, e.g. `await UserFactory.create_async(is_admin=True)`.
The integration conftest binds `__async_session__` to the test's session.
"""

from decimal import Decimal
from typing import Any
from uuid import uuid4

from polyfactory import Ignore, Use
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory

from eve.auth.models import User
from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.core.config import Settings
from eve.core.db import Base
from eve.core.security import TokenType, create_token, hash_password

TEST_PASSWORD = "Password123"
# Argon2 is deliberately slow: hash the shared test password once, not per user.
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


class ModelFactory[T: Base](SQLAlchemyFactory[T]):
    """Shared factory settings for every ORM model."""

    __is_base_factory__ = True
    __set_primary_key__ = False  # use the model's UUIDv7 default
    # Never invent related rows implicitly: tests create exactly the data they need.
    __set_relationships__ = False
    created_at = Ignore()  # server defaults
    updated_at = Ignore()


class UserFactory(ModelFactory[User]):
    email = Use(lambda: f"user-{uuid4().hex[:12]}@example.com")
    password_hash = Use(lambda: TEST_PASSWORD_HASH)
    full_name = Use(lambda: UserFactory.__faker__.name())
    phone = None
    is_active = True
    is_admin = False


class CentreFactory(ModelFactory[DiagnosticCentre]):
    name = Use(lambda: f"Centre {uuid4().hex[:8]}")
    address = "1 Test Road"
    city = "Mumbai"
    pincode = "400053"
    is_active = True


class DiagnosticTestFactory(ModelFactory[DiagnosticTest]):
    code = Use(lambda: f"T{uuid4().hex[:8].upper()}")
    name = Use(lambda: f"Test {uuid4().hex[:8]}")
    description = None
    is_active = True


class OfferingFactory(ModelFactory[CentreTest]):
    """Pass `centre_id` and `test_id` explicitly."""

    price = Decimal("499.00")
    currency = "INR"
    is_available = True


# Every factory whose `__async_session__` the integration conftest binds per test.
PERSISTED_FACTORIES: tuple[type[ModelFactory[Any]], ...] = (
    UserFactory,
    CentreFactory,
    DiagnosticTestFactory,
    OfferingFactory,
)


class AuthHeaders:
    """Builds `Authorization` headers for a user without a login round-trip."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def __call__(self, user: User, token_type: TokenType = TokenType.ACCESS) -> dict[str, str]:
        token = create_token(user.id, token_type, self._settings).token
        return {"Authorization": f"Bearer {token}"}


def make_settings(**overrides: Any) -> Settings:
    """Settings for unit tests: no `.env`, no real database connection."""
    values: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/eve",
        "jwt_secret_key": "unit-test-secret-that-is-at-least-32-bytes",
    }
    return Settings(_env_file=None, **(values | overrides))
