from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eve.bookings.models import Booking
from eve.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin, status_check, status_enum
from eve.core.state_machine import StateMachine


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


PAYMENT_STATES = StateMachine[PaymentStatus](
    "Payment",
    {
        PaymentStatus.PENDING: frozenset({PaymentStatus.SUCCESS, PaymentStatus.FAILED}),
        PaymentStatus.SUCCESS: frozenset(),
        PaymentStatus.FAILED: frozenset(),
    },
)


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt to pay for a booking. A booking may have several failed attempts but,
    enforced by the database, never more than one successful one."""

    __tablename__ = "payments"
    __table_args__ = (
        Index(
            "uq_payments_one_success_per_booking",
            "booking_id",
            unique=True,
            postgresql_where=text("status = 'SUCCESS'"),
        ),
        Index(
            "uq_payments_booking_idempotency_key",
            "booking_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        status_check(PaymentStatus),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
    )

    booking_id: Mapped[UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[PaymentStatus] = mapped_column(
        status_enum(PaymentStatus),
        default=PaymentStatus.PENDING,
    )
    provider: Mapped[str] = mapped_column(String(32))
    # The gateway's own ID for this payment; webhooks refer to payments by it.
    provider_reference: Mapped[str] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    completed_at: Mapped[datetime | None]

    booking: Mapped[Booking] = relationship(lazy="raise")

    def record_result(self, status: PaymentStatus, *, failure_reason: str | None = None) -> bool:
        """Move to a final status through the state machine; False if already there."""
        if not PAYMENT_STATES.check(self.status, status):
            return False
        self.status = status
        self.failure_reason = failure_reason if status is PaymentStatus.FAILED else None
        self.completed_at = datetime.now(UTC)
        return True


class WebhookEventStatus(StrEnum):
    RECEIVED = "RECEIVED"  # stored, not yet processed
    PROCESSED = "PROCESSED"  # applied (or confirmed to be already applied)
    IGNORED = "IGNORED"  # valid but deliberately not applied (see `outcome`)
    FAILED = "FAILED"  # could not be applied; needs investigation or a replay


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable inbox of provider events, and their audit trail.

    `event_id` is the provider's own ID for the event; its UNIQUE constraint is what makes
    redelivery of the same event a no-op.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        status_check(WebhookEventStatus),
        # Serves the sweeper that re-queues events stuck in RECEIVED.
        Index("ix_webhook_events_status_created_at", "status", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[WebhookEventStatus] = mapped_column(
        status_enum(WebhookEventStatus), default=WebhookEventStatus.RECEIVED
    )
    outcome: Mapped[str | None] = mapped_column(String(50))
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    payment_id: Mapped[UUID | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"))
    processed_at: Mapped[datetime | None]
