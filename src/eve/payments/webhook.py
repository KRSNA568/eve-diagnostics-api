"""Provider webhooks: a durable inbox plus an idempotent processor.

Receiving and processing are separate steps. `receive()` only stores the event (deduplicated
by the provider's event_id) and returns quickly; `process()` applies it. That split is what
later lets processing move to a background worker with retries.

Duplicate deliveries cannot corrupt state, by five independent mechanisms:
  1. HMAC signature (router)                 - forged events never reach this module
  2. UNIQUE(webhook_events.event_id)         - the same event is stored and applied once
  3. UNIQUE(payments.provider_reference)     - the same payment is never created twice
  4. state machines                          - terminal states never regress; repeats no-op
  5. row locks + one-SUCCESS-per-booking     - concurrent deliveries serialise
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.bookings.models import Booking
from eve.bookings.repository import BookingRepository
from eve.bookings.state import BookingStatus
from eve.payments.gateway import MockPaymentGateway
from eve.payments.models import Payment, PaymentStatus, WebhookEvent, WebhookEventStatus
from eve.payments.repository import PaymentRepository, WebhookEventRepository
from eve.payments.schemas import WebhookAck, WebhookEventIn, WebhookEventType, WebhookPaymentData
from eve.payments.service import apply_payment_result

logger = structlog.get_logger(__name__)

_TARGET_STATUS = {
    WebhookEventType.PAYMENT_SUCCEEDED: PaymentStatus.SUCCESS,
    WebhookEventType.PAYMENT_FAILED: PaymentStatus.FAILED,
}


class Outcome(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    RECORDED_BOOKING_CLOSED = "recorded_booking_closed"
    REFUND_REQUIRED = "refund_required"
    DUPLICATE_CHARGE_REFUND_REQUIRED = "duplicate_charge_refund_required"
    CONFLICTING_STATUS = "conflicting_status"
    UNSUPPORTED_EVENT_TYPE = "unsupported_event_type"


class WebhookRejectedError(Exception):
    """The event can never be applied as sent (not a transient failure: retrying won't help)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Result:
    status: WebhookEventStatus
    outcome: Outcome
    payment: Payment | None = None


class WebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = WebhookEventRepository(session)
        self._bookings = BookingRepository(session)
        self._payments = PaymentRepository(session)

    async def receive(
        self, event: WebhookEventIn, raw_payload: dict[str, Any]
    ) -> tuple[WebhookAck, UUID | None]:
        """Store the event. Returns the ack and the stored row's id (None for a duplicate)."""
        event_pk = await self._events.insert_if_new(
            event_id=event.event_id, event_type=event.type, payload=raw_payload
        )
        await self._session.commit()

        log = logger.bind(event_id=event.event_id, event_type=event.type)
        if event_pk is None:
            log.info("webhook.duplicate")
            return WebhookAck(status="duplicate", event_id=event.event_id), None
        log.info("webhook.received")
        return WebhookAck(status="accepted", event_id=event.event_id), event_pk

    async def process(self, event_pk: UUID) -> WebhookEventStatus | None:
        """Apply a stored event. Safe to call any number of times for the same event."""
        event = await self._events.get_for_update(event_pk)
        if event is None:
            return None
        if event.status in (WebhookEventStatus.PROCESSED, WebhookEventStatus.IGNORED):
            final_status = event.status  # read before rollback expires the object
            await self._session.rollback()  # release the row lock
            return final_status

        event.attempts += 1
        log = logger.bind(event_id=event.event_id, event_type=event.event_type)
        try:
            result = await self._apply(event)
        except WebhookRejectedError as exc:
            event.status = WebhookEventStatus.FAILED
            event.last_error = exc.reason
            await self._session.commit()
            log.warning("webhook.rejected", reason=exc.reason)
            return event.status

        if result.payment is not None:
            await self._session.flush()  # assigns the id of a payment created just now
        event.status = result.status
        event.outcome = result.outcome
        event.payment_id = result.payment.id if result.payment else None
        event.last_error = None
        event.processed_at = datetime.now(UTC)
        await self._session.commit()

        level = (
            "info" if result.outcome in (Outcome.APPLIED, Outcome.ALREADY_APPLIED) else "warning"
        )
        getattr(log, level)("webhook.processed", status=result.status, outcome=result.outcome)
        return event.status

    async def _apply(self, event: WebhookEvent) -> _Result:
        try:
            event_type = WebhookEventType(event.event_type)
        except ValueError:
            return _Result(WebhookEventStatus.IGNORED, Outcome.UNSUPPORTED_EVENT_TYPE)
        try:
            data = WebhookPaymentData.model_validate(event.payload.get("data"))
        except ValidationError as exc:
            raise WebhookRejectedError("invalid_payload") from exc
        target = _TARGET_STATUS[event_type]

        # Lock order matches POST /payments/ (booking, then payment): no deadlocks.
        booking = await self._bookings.get(data.booking_id, for_update=True)
        if booking is None:
            raise WebhookRejectedError("unknown_booking")
        payment = await self._resolve_payment(booking, data)

        if payment.status == target:
            return _Result(WebhookEventStatus.PROCESSED, Outcome.ALREADY_APPLIED, payment)
        if payment.status is not PaymentStatus.PENDING:
            # e.g. payment.failed arriving after payment.succeeded: final states never regress.
            return _Result(WebhookEventStatus.IGNORED, Outcome.CONFLICTING_STATUS, payment)
        if booking.status is not BookingStatus.PENDING:
            return await self._apply_to_closed_booking(booking, payment, target, data)

        self._payments.add(payment)  # no-op if it already exists
        apply_payment_result(booking, payment, target, data.failure_reason)
        return _Result(WebhookEventStatus.PROCESSED, Outcome.APPLIED, payment)

    async def _resolve_payment(self, booking: Booking, data: WebhookPaymentData) -> Payment:
        """The payment the event refers to. One the provider reports but we have never seen
        (e.g. the client's own confirmation never reached us) is built here but only added to
        the session once the caller decides to record it."""
        payment = await self._payments.get_by_reference(data.provider_reference)
        if payment is not None:
            if payment.booking_id != booking.id:
                raise WebhookRejectedError("booking_mismatch")
            if payment.amount != data.amount or payment.currency != data.currency:
                raise WebhookRejectedError("amount_mismatch")
            return payment

        if data.amount != booking.amount or data.currency != booking.currency:
            raise WebhookRejectedError("amount_mismatch")
        return Payment(
            booking=booking,
            amount=data.amount,
            currency=data.currency,
            status=PaymentStatus.PENDING,
            provider=MockPaymentGateway.name,
            provider_reference=data.provider_reference,
        )

    async def _apply_to_closed_booking(
        self,
        booking: Booking,
        payment: Payment,
        target: PaymentStatus,
        data: WebhookPaymentData,
    ) -> _Result:
        """The booking already left PENDING (paid, failed, cancelled or expired). Record what
        happened to the money truthfully, but never reopen the booking."""
        log = logger.bind(booking_id=str(booking.id), provider_reference=data.provider_reference)

        if target is PaymentStatus.SUCCESS and await self._payments.has_successful_payment(
            booking.id
        ):
            # A second successful charge for a booking that is already paid. The database
            # allows only one SUCCESS per booking, so it is not recorded as such; the event
            # row keeps the full payload for reconciliation.
            log.warning("payment.duplicate_charge_refund_required")
            return _Result(WebhookEventStatus.IGNORED, Outcome.DUPLICATE_CHARGE_REFUND_REQUIRED)

        self._payments.add(payment)
        payment.record_result(target, failure_reason=data.failure_reason)
        if target is PaymentStatus.SUCCESS:
            # Money was taken for a booking that will not happen (e.g. it expired unpaid).
            log.warning("payment.refund_required", booking_status=booking.status.value)
            return _Result(WebhookEventStatus.PROCESSED, Outcome.REFUND_REQUIRED, payment)
        return _Result(WebhookEventStatus.PROCESSED, Outcome.RECORDED_BOOKING_CLOSED, payment)
