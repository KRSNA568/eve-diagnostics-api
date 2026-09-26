"""Background jobs for bookings, run by the ARQ worker."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from eve.core.config import Settings
from eve.core.queue import TaskQueue

logger = structlog.get_logger(__name__)

BOOKING_CONFIRMATION_JOB = "send_booking_confirmation"
EXPIRY_BATCH_SIZE = 500


def confirmation_job_id(booking_id: UUID) -> str:
    """One confirmation per booking, however many paths confirm it (API + webhook)."""
    return f"booking-confirmation:{booking_id}"


async def notify_booking_confirmed(queue: TaskQueue | None, booking_id: UUID) -> None:
    """Queue the confirmation. Call only after the confirming transaction has committed.

    Best effort: the booking is already confirmed, so a queue outage must not fail the
    request that confirmed it (a transactional outbox would make this guaranteed).
    """
    if queue is None:
        return
    try:
        await queue.enqueue(
            BOOKING_CONFIRMATION_JOB, str(booking_id), job_id=confirmation_job_id(booking_id)
        )
    except Exception:
        logger.exception("notification.enqueue_failed", booking_id=str(booking_id))


def mask_email(email: str) -> str:
    """Keep personal data out of logs: a***@example.com."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


async def expire_unpaid_bookings(ctx: dict[str, Any]) -> int:
    """Cancel PENDING bookings whose payment window has passed, releasing their slot.

    FOR UPDATE SKIP LOCKED: a booking locked by an in-flight payment is skipped rather than
    waited on - that payment decides its fate, and the next run catches it if needed.
    """
    settings: Settings = ctx["settings"]
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.booking_payment_window_minutes)

    async with ctx["session_factory"]() as session:
        stmt = (
            select(Booking)
            .where(Booking.status == BookingStatus.PENDING, Booking.created_at < cutoff)
            .order_by(Booking.created_at)
            .limit(EXPIRY_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        bookings = (await session.execute(stmt)).scalars().all()
        for booking in bookings:
            booking.transition_to(BookingStatus.CANCELLED, reason="payment_window_expired")
        await session.commit()

    if bookings:
        logger.info(
            "booking.expired", count=len(bookings), booking_ids=[str(b.id) for b in bookings]
        )
    return len(bookings)


async def send_booking_confirmation(ctx: dict[str, Any], booking_id: str) -> str:
    """Notify the patient that their booking is confirmed.

    Stand-in for an email/SMS provider: emits a structured event. Re-checks the booking
    first, since it may have been cancelled between being queued and running.
    """
    async with ctx["session_factory"]() as session:
        stmt = (
            select(Booking, User.email)
            .join(User, User.id == Booking.user_id)
            .options(joinedload(Booking.centre), joinedload(Booking.test))
            .where(Booking.id == UUID(booking_id))
        )
        row = (await session.execute(stmt)).one_or_none()

    if row is None or row.Booking.status is not BookingStatus.CONFIRMED:
        logger.info("notification.skipped", booking_id=booking_id)
        return "skipped"

    booking = row.Booking
    logger.info(
        "notification.booking_confirmed",
        booking_id=booking_id,
        recipient=mask_email(row.email),
        test=booking.test.code,
        centre=booking.centre.name,
        appointment_at=booking.appointment_at.isoformat(),
    )
    return "sent"
