from collections.abc import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.cli.__main__ import main
from eve.cli.commands import DEMO_CENTRES, DEMO_TESTS, create_admin, seed_catalog
from eve.core.config import Settings, get_settings
from eve.core.security import verify_password
from tests.factories import UserFactory


async def _count(session: AsyncSession, model: type[object]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await seed_catalog(db_session)
    await seed_catalog(db_session)

    assert await _count(db_session, DiagnosticTest) == len(DEMO_TESTS)
    assert await _count(db_session, DiagnosticCentre) == len(DEMO_CENTRES)
    assert await _count(db_session, CentreTest) == sum(len(c[4]) for c in DEMO_CENTRES)


async def test_create_admin_creates_a_new_admin(db_session: AsyncSession) -> None:
    user, created = await create_admin(db_session, "Ops@Example.com", "Str0ngPassword")

    assert created is True
    assert user.is_admin is True
    assert user.email == "ops@example.com"
    assert verify_password("Str0ngPassword", user.password_hash)[0] is True


async def test_create_admin_promotes_an_existing_user(db_session: AsyncSession) -> None:
    existing = await UserFactory.create_async(email="staff@example.com")

    user, created = await create_admin(db_session, "staff@example.com", "Str0ngPassword")

    assert created is False
    assert user.id == existing.id
    assert user.is_admin is True


async def test_create_admin_applies_the_password_policy(db_session: AsyncSession) -> None:
    with pytest.raises(ValidationError, match="password"):
        await create_admin(db_session, "ops@example.com", "weak")


@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, migrated_database: None
) -> Iterator[None]:
    """Point the real CLI entry point (which reads settings from the environment) at the
    test database."""
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("JWT_SECRET_KEY", settings.jwt_secret_key.get_secret_value())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("cli_env")
def test_cli_seed_command(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["seed"])

    assert exit_code == 0
    assert "Seeded catalog: 6 tests, 4 centres" in capsys.readouterr().out


@pytest.mark.usefixtures("cli_env")
def test_cli_create_admin_reads_password_from_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "Str0ngPassword")

    exit_code = main(["create-admin", "--email", "ops@example.com"])

    assert exit_code == 0
    assert "Created admin: ops@example.com" in capsys.readouterr().out


@pytest.mark.usefixtures("cli_env")
def test_cli_create_admin_rejects_weak_passwords(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "weak")

    exit_code = main(["create-admin", "--email", "ops@example.com"])

    assert exit_code == 2
    assert "Invalid input" in capsys.readouterr().err
