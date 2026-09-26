import pytest
from pydantic import ValidationError

from tests.factories import make_settings


@pytest.mark.parametrize(
    "raw_url",
    [
        "postgres://user:pw@db.example.com:6543/postgres",
        "postgresql://user:pw@db.example.com:6543/postgres",
    ],
)
def test_plain_postgres_urls_are_routed_to_the_psycopg_driver(raw_url: str) -> None:
    settings = make_settings(database_url=raw_url)

    assert settings.database_url == "postgresql+psycopg://user:pw@db.example.com:6543/postgres"


def test_explicit_driver_urls_are_left_untouched() -> None:
    url = "postgresql+psycopg://user:pw@localhost:5432/eve"

    assert make_settings(database_url=url).database_url == url


def test_migrations_url_falls_back_to_database_url() -> None:
    settings = make_settings(database_url="postgresql://u:p@pooler:6543/postgres")

    assert settings.migrations_url == "postgresql+psycopg://u:p@pooler:6543/postgres"


def test_migrations_url_prefers_dedicated_session_url() -> None:
    settings = make_settings(
        database_url="postgresql://u:p@pooler:6543/postgres",
        migrations_database_url="postgresql://u:p@pooler:5432/postgres",
    )

    assert settings.migrations_url == "postgresql+psycopg://u:p@pooler:5432/postgres"


def test_secrets_are_hidden_from_repr() -> None:
    settings = make_settings(
        database_url="postgresql://u:db-secret@host/db",
        jwt_secret_key="jwt-secret-that-is-at-least-32-bytes-long",
    )

    assert "db-secret" not in repr(settings)
    assert "jwt-secret" not in repr(settings)


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="jwt_secret_key"):
        make_settings(jwt_secret_key="too-short")
