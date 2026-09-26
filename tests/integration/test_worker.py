"""The asynchronous path end to end: HTTP -> Redis queue -> ARQ worker -> database."""

from typing import Any
from uuid import uuid4

import pytest
from arq.connections import ArqRedis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.state import BookingStatus
from eve.cli.commands import build_payment_event, send_webhook
from eve.core.config import Settings
from eve.core.queue import ArqTaskQueue
from eve.payments.jobs import PROCESS_WEBHOOK_JOB, webhook_job_id
from eve.payments.models import WebhookEvent, WebhookEventStatus
from eve.payments.webhook import WebhookService
from tests.conftest import TEST_WEBHOOK_SECRET
from tests.factories import AuthHeaders, Offering, book
from tests.integration.conftest import RunWorker

WEBHOOK = "/api/v1/payments/webhook/"
EVENTS = "/api/v1/payments/webhook/events/"


async def event_row(session: AsyncSession, event_id: str) -> WebhookEvent:
    stmt = (
        select(WebhookEvent)
        .where(WebhookEvent.event_id == event_id)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def deliver(client: AsyncClient, event: dict[str, Any]) -> None:
    await send_webhook(client, WEBHOOK, event, TEST_WEBHOOK_SECRET)


def fail_first(monkeypatch: pytest.MonkeyPatch, failures: int) -> list[int]:
    """Make WebhookService.process raise a transient error for its first `failures` calls."""
    calls: list[int] = []
    original = WebhookService.process

    async def flaky(self: WebhookService, event_pk: Any) -> Any:
        calls.append(1)
        if len(calls) <= failures:
            raise ConnectionError("database connection lost")
        return await original(self, event_pk)

    monkeypatch.setattr(WebhookService, "process", flaky)
    return calls


async def test_webhook_is_queued_and_processed_by_the_worker(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    run_worker: RunWorker,
) -> None:
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)

    await deliver(client, event)
    assert (await event_row(db_session, event["event_id"])).status is WebhookEventStatus.RECEIVED

    await run_worker()

    assert (await event_row(db_session, event["event_id"])).status is WebhookEventStatus.PROCESSED
    await db_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED


async def test_transient_failures_are_retried_until_they_succeed(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fail_first(monkeypatch, failures=2)
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)

    await deliver(client, event)
    await run_worker()

    assert len(calls) == 3  # two failures, then success within webhook_max_attempts=3
    row = await event_row(db_session, event["event_id"])
    assert row.status is WebhookEventStatus.PROCESSED


async def test_event_is_dead_lettered_after_the_last_attempt(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    run_worker: RunWorker,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fail_first(monkeypatch, failures=100)
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)

    await deliver(client, event)
    await run_worker()

    assert len(calls) == settings.webhook_max_attempts
    row = await event_row(db_session, event["event_id"])
    assert row.status is WebhookEventStatus.FAILED
    assert row.attempts == settings.webhook_max_attempts
    assert row.last_error == "ConnectionError: database connection lost"
    await db_session.refresh(booking)
    assert booking.status is BookingStatus.PENDING


async def test_the_same_event_cannot_be_queued_twice(settings: Settings) -> None:
    redis = ArqRedis.from_url(settings.redis_url)
    queue = ArqTaskQueue(redis)
    job_id = webhook_job_id(uuid4())
    try:
        first = await queue.enqueue(PROCESS_WEBHOOK_JOB, "x", job_id=job_id)
        second = await queue.enqueue(PROCESS_WEBHOOK_JOB, "x", job_id=job_id)
    finally:
        await redis.aclose()

    assert (first, second) == (True, False)


# --------------------------------------------------------------------------- operations


async def test_admin_can_list_dead_letters_and_replay_them(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    admin: User,
    auth_headers: AuthHeaders,
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_first(monkeypatch, failures=3)  # every attempt of the first run fails
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)
    await deliver(client, event)
    await run_worker()

    dead = await client.get(EVENTS, params={"status": "FAILED"}, headers=auth_headers(admin))
    assert [e["event_id"] for e in dead.json()["items"]] == [event["event_id"]]

    replay = await client.post(
        f"{EVENTS}{dead.json()['items'][0]['id']}/replay/", headers=auth_headers(admin)
    )
    assert replay.status_code == 202
    assert replay.json()["status"] == "RECEIVED"
    await run_worker()  # the transient problem is gone now

    assert (await event_row(db_session, event["event_id"])).status is WebhookEventStatus.PROCESSED
    await db_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED


async def test_processed_events_cannot_be_replayed(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    admin: User,
    auth_headers: AuthHeaders,
    run_worker: RunWorker,
) -> None:
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)
    await deliver(client, event)
    await run_worker()
    row = await event_row(db_session, event["event_id"])

    response = await client.post(f"{EVENTS}{row.id}/replay/", headers=auth_headers(admin))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EVENT_ALREADY_PROCESSED"


async def test_webhook_operations_are_admin_only(
    client: AsyncClient, user: User, admin: User, auth_headers: AuthHeaders
) -> None:
    unknown = "01a0dcae-0000-7000-8000-000000000000"

    listing = await client.get(EVENTS, headers=auth_headers(user))
    replay = await client.post(f"{EVENTS}{unknown}/replay/", headers=auth_headers(user))
    missing = await client.post(f"{EVENTS}{unknown}/replay/", headers=auth_headers(admin))

    assert (listing.status_code, replay.status_code) == (403, 403)
    assert missing.status_code == 404


async def test_webhook_is_still_acknowledged_when_redis_is_down(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def redis_down(self: ArqTaskQueue, *args: Any, **kwargs: Any) -> bool:
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(ArqTaskQueue, "enqueue", redis_down)
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)

    response = await send_webhook(client, WEBHOOK, event, TEST_WEBHOOK_SECRET)

    assert response[0].status_code == 202  # stored durably; processed once Redis is back
    assert (await event_row(db_session, event["event_id"])).status is WebhookEventStatus.RECEIVED


@pytest.mark.usefixtures("app_env")
async def test_worker_entrypoint_wires_jobs_and_database() -> None:
    from eve.worker.registry import shutdown, startup
    from eve.worker.settings import WorkerSettings

    assert [f.name for f in WorkerSettings.functions] == [PROCESS_WEBHOOK_JOB]
    ctx: dict[str, Any] = {}
    await startup(ctx)
    try:
        async with ctx["session_factory"]() as session:
            assert (await session.execute(select(1))).scalar_one() == 1
    finally:
        await shutdown(ctx)
