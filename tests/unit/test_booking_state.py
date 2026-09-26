from decimal import Decimal
from itertools import product

import pytest

from eve.bookings.models import Booking
from eve.bookings.state import BOOKING_STATES, BookingStatus
from eve.core.state_machine import InvalidStatusTransitionError

S = BookingStatus
ALLOWED = {
    (S.PENDING, S.CONFIRMED),
    (S.PENDING, S.FAILED),
    (S.PENDING, S.CANCELLED),
    (S.CONFIRMED, S.CANCELLED),
}


@pytest.mark.parametrize(("current", "target"), list(product(S, S)))
def test_transition_table(current: BookingStatus, target: BookingStatus) -> None:
    if current == target:
        assert BOOKING_STATES.check(current, target) is False  # idempotent no-op
    elif (current, target) in ALLOWED:
        assert BOOKING_STATES.check(current, target) is True
    else:
        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            BOOKING_STATES.check(current, target)
        assert exc_info.value.status_code == 409
        assert exc_info.value.details == {"from": current.value, "to": target.value}


@pytest.mark.parametrize("status", [S.FAILED, S.CANCELLED])
def test_failed_and_cancelled_are_terminal(status: BookingStatus) -> None:
    assert BOOKING_STATES.is_terminal(status)


def _booking(status: BookingStatus = S.PENDING) -> Booking:
    return Booking(status=status, amount=Decimal("1.00"), currency="INR")


def test_confirming_records_when() -> None:
    booking = _booking()

    assert booking.transition_to(S.CONFIRMED) is True
    assert booking.status is S.CONFIRMED
    assert booking.confirmed_at is not None
    assert booking.cancelled_at is None


def test_cancelling_records_when_and_why() -> None:
    booking = _booking(S.CONFIRMED)

    booking.transition_to(S.CANCELLED, reason="cancelled_by_user")

    assert booking.cancelled_at is not None
    assert booking.status_reason == "cancelled_by_user"


def test_repeating_a_transition_changes_nothing() -> None:
    booking = _booking(S.CANCELLED)
    booking.status_reason = "original"

    assert booking.transition_to(S.CANCELLED, reason="again") is False
    assert booking.status_reason == "original"
    assert booking.cancelled_at is None
