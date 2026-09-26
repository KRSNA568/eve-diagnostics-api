import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.cli.__main__ import main
from eve.cli.commands import DEMO_CENTRES, DEMO_TESTS, create_admin, seed_catalog
from eve.core.config import Settings
from eve.core.security import verify_password
from eve.payments.webhook_signature import SIGNATURE_HEADER, verify
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


@pytest.mark.usefixtures("app_env")
def test_cli_seed_command(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["seed"])

    assert exit_code == 0
    assert "Seeded catalog: 6 tests, 4 centres" in capsys.readouterr().out


@pytest.mark.usefixtures("app_env")
def test_cli_create_admin_reads_password_from_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "Str0ngPassword")

    exit_code = main(["create-admin", "--email", "ops@example.com"])

    assert exit_code == 0
    assert "Created admin: ops@example.com" in capsys.readouterr().out


@pytest.mark.usefixtures("app_env")
def test_cli_create_admin_rejects_weak_passwords(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "weak")

    exit_code = main(["create-admin", "--email", "ops@example.com"])

    assert exit_code == 2
    assert "Invalid input" in capsys.readouterr().err


@pytest.mark.usefixtures("app_env")
def test_cli_send_webhook_signs_and_repeats_the_event(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    received: list[httpx.Request] = []

    def provider_endpoint(request: httpx.Request) -> httpx.Response:
        received.append(request)
        verify(
            request.headers[SIGNATURE_HEADER],
            request.content,
            settings.webhook_secret.get_secret_value(),
            tolerance_seconds=60,
        )
        return httpx.Response(202 if len(received) == 1 else 200, json={"ok": True})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "eve.cli.__main__.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(provider_endpoint), **kw),
    )
    booking_id = "01a0dcae-0000-7000-8000-000000000000"

    args = ["send-webhook", "--booking-id", booking_id, "--outcome", "failed"]
    args += ["--amount", "349.00", "--event-id", "evt_cli_1", "--repeat", "2"]

    exit_code = main(args)

    assert exit_code == 0
    assert len(received) == 2
    assert received[0].content == received[1].content  # the same event, delivered twice
    body = json.loads(received[0].content)
    assert (body["event_id"], body["type"]) == ("evt_cli_1", "payment.failed")
    assert body["data"]["booking_id"] == booking_id
    output = capsys.readouterr().out
    assert "delivery 1: HTTP 202" in output
    assert "delivery 2: HTTP 200" in output
