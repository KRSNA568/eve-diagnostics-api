from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, text
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
