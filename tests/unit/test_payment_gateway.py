import random
from decimal import Decimal

import pytest

from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from eve.core.state_machine import InvalidStatusTransitionError
from eve.payments.gateway import ChargeRequest, MockPaymentGateway, MockPaymentMethod
from eve.payments.models import Payment, PaymentStatus
from eve.payments.service import apply_payment_result


def _request(method: MockPaymentMethod) -> ChargeRequest:
    return ChargeRequest(
        reference="mock_pay_1", amount=Decimal("499.00"), currency="INR", payment_method=method
    )


async def test_success_method_is_always_approved() -> None:
    result = await MockPaymentGateway(success_rate=0).charge(_request(MockPaymentMethod.SUCCESS))

    assert result.status is PaymentStatus.SUCCESS
    assert result.failure_reason is None


async def test_declined_method_is_always_declined() -> None:
    result = await MockPaymentGateway(success_rate=1).charge(_request(MockPaymentMethod.DECLINED))

    assert result.status is PaymentStatus.FAILED
    assert result.failure_reason == "card_declined"


async def _random_outcomes(gateway: MockPaymentGateway, n: int) -> list[PaymentStatus]:
    return [(await gateway.charge(_request(MockPaymentMethod.RANDOM))).status for _ in range(n)]


@pytest.mark.parametrize(
    ("rate", "expected"), [(1.0, PaymentStatus.SUCCESS), (0.0, PaymentStatus.FAILED)]
)
async def test_random_method_follows_the_configured_rate(
    rate: float, expected: PaymentStatus
) -> None:
    outcomes = await _random_outcomes(MockPaymentGateway(success_rate=rate), 20)

    assert set(outcomes) == {expected}


async def test_random_method_is_reproducible_with_a_seeded_rng() -> None:
    first = await _random_outcomes(MockPaymentGateway(0.5, rng=random.Random(7)), 20)
    second = await _random_outcomes(MockPaymentGateway(0.5, rng=random.Random(7)), 20)

    assert first == second
    assert set(first) == {PaymentStatus.SUCCESS, PaymentStatus.FAILED}


def test_success_rate_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        MockPaymentGateway(success_rate=1.5)


def test_provider_references_are_unique() -> None:
    gateway = MockPaymentGateway(success_rate=1)

    references = {gateway.new_reference() for _ in range(100)}

    assert len(references) == 100
    assert all(ref.startswith("mock_pay_") for ref in references)


# --------------------------------------------------------------------------- result logic


def _pending() -> tuple[Booking, Payment]:
    booking = Booking(status=BookingStatus.PENDING, amount=Decimal("1.00"), currency="INR")
    payment = Payment(status=PaymentStatus.PENDING, amount=Decimal("1.00"), currency="INR")
    return booking, payment


def test_successful_payment_confirms_the_booking() -> None:
    booking, payment = _pending()

    assert apply_payment_result(booking, payment, PaymentStatus.SUCCESS) is True
    assert payment.status is PaymentStatus.SUCCESS
    assert payment.completed_at is not None
    assert booking.status is BookingStatus.CONFIRMED


def test_failed_payment_fails_the_booking_with_the_reason() -> None:
    booking, payment = _pending()

    apply_payment_result(booking, payment, PaymentStatus.FAILED, "card_declined")

    assert payment.failure_reason == "card_declined"
    assert booking.status is BookingStatus.FAILED
    assert booking.status_reason == "payment_failed:card_declined"


def test_repeating_the_same_result_is_a_no_op() -> None:
    booking, payment = _pending()
    apply_payment_result(booking, payment, PaymentStatus.SUCCESS)
    completed_at = payment.completed_at

    assert apply_payment_result(booking, payment, PaymentStatus.SUCCESS) is False
    assert payment.completed_at == completed_at


def test_a_final_payment_result_cannot_be_reversed() -> None:
    booking, payment = _pending()
    apply_payment_result(booking, payment, PaymentStatus.SUCCESS)

    with pytest.raises(InvalidStatusTransitionError):
        apply_payment_result(booking, payment, PaymentStatus.FAILED, "late_decline")
    assert booking.status is BookingStatus.CONFIRMED
