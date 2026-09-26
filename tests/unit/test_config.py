import pytest

from eve.core.config import Settings


@pytest.mark.parametrize(
    "raw_url",
    [
        "postgres://user:pw@db.example.com:6543/postgres",
        "postgresql://user:pw@db.example.com:6543/postgres",
    ],
)
def test_plain_postgres_urls_are_routed_to_the_psycopg_driver(raw_url: str) -> None:
    settings = Settings(_env_file=None, database_url=raw_url)

    assert settings.database_url == "postgresql+psycopg://user:pw@db.example.com:6543/postgres"


def test_explicit_driver_urls_are_left_untouched() -> None:
    url = "postgresql+psycopg://user:pw@localhost:5432/eve"

    assert Settings(_env_file=None, database_url=url).database_url == url


def test_migrations_url_falls_back_to_database_url() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://u:p@pooler:6543/postgres")

    assert settings.migrations_url == "postgresql+psycopg://u:p@pooler:6543/postgres"


def test_migrations_url_prefers_dedicated_session_url() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://u:p@pooler:6543/postgres",
        migrations_database_url="postgresql://u:p@pooler:5432/postgres",
    )

    assert settings.migrations_url == "postgresql+psycopg://u:p@pooler:5432/postgres"


def test_database_url_is_hidden_from_repr() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://u:secret@host/db")

    assert "secret" not in repr(settings)
