from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eve.bookings.state import ACTIVE_STATUSES, BOOKING_STATES, BookingStatus
from eve.catalog.models import DiagnosticCentre, DiagnosticTest
from eve.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin, status_check, status_enum

_ACTIVE = ", ".join(f"'{s.value}'" for s in ACTIVE_STATUSES)


class Booking(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # The (centre, test) pair must be a real offering: the database - not just the
        # service - refuses a booking for a test the centre does not offer.
        ForeignKeyConstraint(
            ["centre_id", "test_id"],
            ["centre_tests.centre_id", "centre_tests.test_id"],
            name="fk_bookings_offering",
            ondelete="RESTRICT",
        ),
        # One active booking per user/slot: blocks double-submits even under concurrency,
        # while still allowing a rebook after a failure or cancellation.
        Index(
            "uq_bookings_active_slot",
            "user_id",
            "centre_id",
            "test_id",
            "appointment_at",
            unique=True,
            postgresql_where=text(f"status IN ({_ACTIVE})"),
        ),
        Index("ix_bookings_user_id_created_at", "user_id", "created_at"),
        # Serves the job that expires unpaid PENDING bookings.
        Index("ix_bookings_status_created_at", "status", "created_at"),
        status_check(BookingStatus),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    centre_id: Mapped[UUID] = mapped_column(
        ForeignKey("diagnostic_centres.id", ondelete="RESTRICT")
    )
    test_id: Mapped[UUID] = mapped_column(ForeignKey("diagnostic_tests.id", ondelete="RESTRICT"))
    appointment_at: Mapped[datetime]
    # Price at the moment of booking; later catalog price changes do not affect it.
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[BookingStatus] = mapped_column(
        status_enum(BookingStatus),
        default=BookingStatus.PENDING,
    )
    status_reason: Mapped[str | None] = mapped_column(String(200))
    confirmed_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]

    centre: Mapped[DiagnosticCentre] = relationship(lazy="raise")
    test: Mapped[DiagnosticTest] = relationship(lazy="raise")

    def transition_to(self, target: BookingStatus, *, reason: str | None = None) -> bool:
        """Apply a status change through the state machine.

        Returns False (and changes nothing) if the booking is already in `target`.
        """
        if not BOOKING_STATES.check(self.status, target):
            return False
        now = datetime.now(UTC)
        self.status = target
        self.status_reason = reason
        if target is BookingStatus.CONFIRMED:
            self.confirmed_at = now
        elif target is BookingStatus.CANCELLED:
            self.cancelled_at = now
        return True
