"""Scheduled jobs (booking expiry, stale-event recovery) and confirmation notifications."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from arq.connections import ArqRedis
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from eve.auth.models import User
from eve.bookings.jobs import expire_unpaid_bookings, send_booking_confirmation
from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from eve.cli.commands import build_payment_event, send_webhook
from eve.core.config import Settings
from eve.core.db import create_session_factory
from eve.payments.jobs import requeue_stale_webhook_events
from eve.payments.models import WebhookEvent, WebhookEventStatus
from tests.conftest import TEST_WEBHOOK_SECRET
from tests.factories import AuthHeaders, Offering, book, in_days
from tests.integration.conftest import RunWorker


@pytest.fixture
async def job_ctx(settings: Settings, db_engine: AsyncEngine) -> AsyncIterator[dict[str, Any]]:
    """The context the ARQ worker passes to every job."""
    redis = ArqRedis.from_url(settings.redis_url)
    yield {
        "settings": settings,
        "session_factory": create_session_factory(db_engine),
        "redis": redis,
    }
    await redis.aclose()


async def age(session: AsyncSession, model: type[Any], row_id: Any, minutes: int) -> None:
    """Pretend a row was created `minutes` ago."""
    await session.execute(
        update(model)
        .where(model.id == row_id)
        .values(created_at=in_days(0) - timedelta(minutes=minutes))
    )
    await session.commit()


async def status_of(session: AsyncSession, booking: Booking) -> BookingStatus:
    await session.refresh(booking)
    return booking.status


def notifications(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        r.msg
        for r in caplog.records
        if isinstance(r.msg, dict) and r.msg.get("event") == "notification.booking_confirmed"
    ]


# --------------------------------------------------------------------------- booking expiry


async def test_unpaid_bookings_expire_after_the_payment_window(
    db_session: AsyncSession, offering: Offering, user: User, job_ctx: dict[str, Any]
) -> None:
    stale = await book(offering, user, appointment_at=in_days(2))
    fresh = await book(offering, user, appointment_at=in_days(3))
    paid = await book(offering, user, appointment_at=in_days(4), status=BookingStatus.CONFIRMED)
    for booking in (stale, paid):
        await age(db_session, Booking, booking.id, minutes=16)

    expired = await expire_unpaid_bookings(job_ctx)

    assert expired == 1
    assert await status_of(db_session, stale) is BookingStatus.CANCELLED
    assert stale.status_reason == "payment_window_expired"
    assert await status_of(db_session, fresh) is BookingStatus.PENDING
    assert await status_of(db_session, paid) is BookingStatus.CONFIRMED
    assert await expire_unpaid_bookings(job_ctx) == 0  # idempotent


async def test_expiry_skips_a_booking_locked_by_an_in_flight_payment(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    offering: Offering,
    user: User,
    job_ctx: dict[str, Any],
) -> None:
    booking = await book(offering, user)
    await age(db_session, Booking, booking.id, minutes=30)

    async with create_session_factory(db_engine)() as payment_in_progress:
        await payment_in_progress.execute(
            select(Booking).where(Booking.id == booking.id).with_for_update()
        )
        assert await expire_unpaid_bookings(job_ctx) == 0  # skipped, not blocked
        await payment_in_progress.rollback()

    assert await expire_unpaid_bookings(job_ctx) == 1  # picked up on the next run


# --------------------------------------------------------------------------- stale events


async def test_stale_received_events_are_requeued_and_processed(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    job_ctx: dict[str, Any],
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)
    # Simulate the queue losing the job: store the event without enqueueing it.
    monkeypatch.setattr("eve.core.queue.ArqTaskQueue.enqueue", _enqueue_nothing)
    await send_webhook(client, "/api/v1/payments/webhook/", event, TEST_WEBHOOK_SECRET)
    monkeypatch.undo()
    row = (
        await db_session.execute(
            select(WebhookEvent).where(WebhookEvent.event_id == event["event_id"])
        )
    ).scalar_one()
    await age(db_session, WebhookEvent, row.id, minutes=5)

    assert await requeue_stale_webhook_events(job_ctx) == 1
    assert await requeue_stale_webhook_events(job_ctx) == 0  # already waiting in the queue
    await run_worker()

    await db_session.refresh(row)
    assert row.status is WebhookEventStatus.PROCESSED
    assert await status_of(db_session, booking) is BookingStatus.CONFIRMED


async def _enqueue_nothing(*args: Any, **kwargs: Any) -> bool:
    return True


async def test_recent_and_finished_events_are_not_requeued(
    db_session: AsyncSession, job_ctx: dict[str, Any]
) -> None:
    for event_id, status, minutes in [
        ("evt_recent", WebhookEventStatus.RECEIVED, 0),
        ("evt_done", WebhookEventStatus.PROCESSED, 60),
    ]:
        event = WebhookEvent(
            event_id=event_id, event_type="payment.succeeded", payload={}, status=status
        )
        db_session.add(event)
        await db_session.commit()
        await age(db_session, WebhookEvent, event.id, minutes=minutes)

    assert await requeue_stale_webhook_events(job_ctx) == 0


# --------------------------------------------------------------------------- notifications


async def test_confirmed_booking_triggers_exactly_one_notification(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
    run_worker: RunWorker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    booking = await book(offering, user)
    paid = await client.post(
        "/api/v1/payments/",
        json={"booking_id": str(booking.id), "payment_method": "mock_card_success"},
        headers=auth_headers(user),
    )
    # The provider's webhook for the same payment arrives too - it must not notify again.
    event = build_payment_event(
        booking_id=booking.id,
        outcome="succeeded",
        amount=booking.amount,
        provider_reference=paid.json()["provider_reference"],
    )
    await send_webhook(client, "/api/v1/payments/webhook/", event, TEST_WEBHOOK_SECRET)

    await run_worker()

    [sent] = notifications(caplog)
    assert sent["booking_id"] == str(booking.id)
    assert sent["recipient"] == f"{user.email[0]}***@example.com"  # no raw email in logs
    assert user.email not in caplog.text


async def test_booking_confirmed_by_webhook_is_notified(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    run_worker: RunWorker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)
    await send_webhook(client, "/api/v1/payments/webhook/", event, TEST_WEBHOOK_SECRET)

    await run_worker()  # processes the webhook, which queues the confirmation, then sends it

    assert [n["booking_id"] for n in notifications(caplog)] == [str(booking.id)]


async def test_no_notification_for_a_booking_cancelled_before_it_was_sent(
    db_session: AsyncSession, offering: Offering, user: User, job_ctx: dict[str, Any]
) -> None:
    booking = await book(offering, user, status=BookingStatus.CANCELLED)

    assert await send_booking_confirmation(job_ctx, str(booking.id)) == "skipped"
