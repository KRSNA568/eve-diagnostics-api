from enum import StrEnum

from eve.core.state_machine import StateMachine


class BookingStatus(StrEnum):
    PENDING = "PENDING"  # created, awaiting payment
    CONFIRMED = "CONFIRMED"  # paid
    FAILED = "FAILED"  # payment failed; terminal - the user books again
    CANCELLED = "CANCELLED"  # by the user, or expired unpaid


# Bookings that hold a slot: at most one per (user, centre, test, time).
ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)

BOOKING_STATES = StateMachine[BookingStatus](
    "Booking",
    {
        BookingStatus.PENDING: frozenset(
            {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED}
        ),
        BookingStatus.CONFIRMED: frozenset({BookingStatus.CANCELLED}),
        BookingStatus.FAILED: frozenset(),
        BookingStatus.CANCELLED: frozenset(),
    },
)
