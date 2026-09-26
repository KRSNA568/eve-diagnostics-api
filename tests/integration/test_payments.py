from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from eve.payments.gateway import MockPaymentGateway
from eve.payments.models import Payment, PaymentStatus
from eve.payments.router import get_payment_gateway
from tests.factories import (
    AuthHeaders,
    Offering,
    PaymentFactory,
    UserFactory,
    book,
    in_days,
)

PAYMENTS = "/api/v1/payments/"
BOOKINGS = "/api/v1/bookings/"


def pay_body(booking: Booking, method: str = "mock_card_success", **extra: Any) -> dict[str, Any]:
    return {"booking_id": str(booking.id), "payment_method": method} | extra


async def payment_count(session: AsyncSession, booking: Booking) -> int:
    stmt = select(func.count()).select_from(Payment).where(Payment.booking_id == booking.id)
    return (await session.execute(stmt)).scalar_one()


# --------------------------------------------------------------------------- outcomes


async def test_successful_payment_confirms_the_booking(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user, amount=offering.row.price)

    response = await client.post(PAYMENTS, json=pay_body(booking), headers=auth_headers(user))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert (body["amount"], body["currency"]) == ("349.00", "INR")
    assert body["provider"] == "mockpay"
    assert body["provider_reference"].startswith("mock_pay_")
    assert body["booking"] == {"id": str(booking.id), "status": "CONFIRMED"}

    detail = await client.get(f"{BOOKINGS}{booking.id}/", headers=auth_headers(user))
    assert detail.json()["status"] == "CONFIRMED"
    assert detail.json()["confirmed_at"] is not None


async def test_declined_payment_fails_the_booking(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    response = await client.post(
        PAYMENTS, json=pay_body(booking, "mock_card_declined"), headers=auth_headers(user)
    )

    assert response.status_code == 201  # the request succeeded; the payment did not
    body = response.json()
    assert (body["status"], body["failure_reason"]) == ("FAILED", "card_declined")
    assert body["booking"]["status"] == "FAILED"


@pytest.fixture
def always_declining_gateway(app: FastAPI) -> Iterator[None]:
    app.dependency_overrides[get_payment_gateway] = lambda: MockPaymentGateway(success_rate=0)
    yield
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("always_declining_gateway")
async def test_random_payment_method_uses_the_configured_gateway(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    response = await client.post(
        PAYMENTS, json={"booking_id": str(booking.id)}, headers=auth_headers(user)
    )

    assert response.json()["status"] == "FAILED"


# --------------------------------------------------------------------------- rejections


@pytest.mark.parametrize(
    "status", [BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED]
)
async def test_only_pending_bookings_can_be_paid(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
    status: BookingStatus,
) -> None:
    booking = await book(offering, user, status=status)

    response = await client.post(PAYMENTS, json=pay_body(booking), headers=auth_headers(user))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BOOKING_NOT_PAYABLE"
    assert response.json()["error"]["details"] == {"booking_status": status.value}
    assert await payment_count(db_session, booking) == 0


async def test_paying_twice_does_not_charge_twice(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    first = await client.post(PAYMENTS, json=pay_body(booking), headers=auth_headers(user))
    second = await client.post(PAYMENTS, json=pay_body(booking), headers=auth_headers(user))

    assert first.status_code == 201
    assert second.status_code == 409
    assert await payment_count(db_session, booking) == 1


async def test_cannot_pay_for_someone_elses_booking(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, await UserFactory.create_async())

    response = await client.post(PAYMENTS, json=pay_body(booking), headers=auth_headers(user))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOKING_NOT_FOUND"


@pytest.mark.parametrize(
    ("booking_id", "expected_status"),
    [("01a0dcae-0000-7000-8000-000000000000", 404), ("not-a-uuid", 422)],
)
async def test_invalid_booking_ids_are_rejected(
    client: AsyncClient,
    user: User,
    auth_headers: AuthHeaders,
    booking_id: str,
    expected_status: int,
) -> None:
    response = await client.post(
        PAYMENTS, json={"booking_id": booking_id}, headers=auth_headers(user)
    )

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "extra",
    [{"amount": "1.00"}, {"payment_method": "real_visa_4242"}],
    ids=["client-supplied-amount", "unknown-payment-method"],
)
async def test_invalid_payment_requests_are_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
    extra: dict[str, Any],
) -> None:
    booking = await book(offering, user)

    response = await client.post(
        PAYMENTS, json=pay_body(booking) | extra, headers=auth_headers(user)
    )

    assert response.status_code == 422


async def test_payment_requires_authentication(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)

    response = await client.post(PAYMENTS, json=pay_body(booking))

    assert response.status_code == 401


async def test_cannot_pay_after_the_appointment_time(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)
    await db_session.execute(
        update(Booking)
        .where(Booking.id == booking.id)
        .values(appointment_at=in_days(0) - timedelta(minutes=5))
    )
    await db_session.commit()

    response = await client.post(PAYMENTS, json=pay_body(booking), headers=auth_headers(user))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPOINTMENT_PASSED"


# --------------------------------------------------------------------------- idempotency


async def test_retrying_with_the_same_idempotency_key_returns_the_original_payment(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)
    headers = auth_headers(user) | {"Idempotency-Key": "checkout-7f3a"}

    first = await client.post(PAYMENTS, json=pay_body(booking), headers=headers)
    retry = await client.post(PAYMENTS, json=pay_body(booking), headers=headers)

    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()
    assert await payment_count(db_session, booking) == 1


async def test_a_new_idempotency_key_is_a_new_attempt(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    await client.post(
        PAYMENTS, json=pay_body(booking), headers=auth_headers(user) | {"Idempotency-Key": "a"}
    )
    second = await client.post(
        PAYMENTS, json=pay_body(booking), headers=auth_headers(user) | {"Idempotency-Key": "b"}
    )

    assert second.status_code == 409


async def test_malformed_idempotency_key_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    response = await client.post(
        PAYMENTS,
        json=pay_body(booking),
        headers=auth_headers(user) | {"Idempotency-Key": "has spaces"},
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------- database guard


async def test_database_allows_at_most_one_successful_payment_per_booking(
    db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    await PaymentFactory.create_async(booking_id=booking.id, status=PaymentStatus.SUCCESS)

    with pytest.raises(IntegrityError, match="uq_payments_one_success_per_booking"):
        await PaymentFactory.create_async(booking_id=booking.id, status=PaymentStatus.SUCCESS)


async def test_failed_attempts_do_not_count_against_the_success_limit(
    db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    await PaymentFactory.create_async(booking_id=booking.id, status=PaymentStatus.FAILED)
    await PaymentFactory.create_async(booking_id=booking.id, status=PaymentStatus.FAILED)

    await PaymentFactory.create_async(booking_id=booking.id, status=PaymentStatus.SUCCESS)

    assert await payment_count(db_session, booking) == 3


# --------------------------------------------------------------------------- reads


async def test_users_only_see_their_own_payments(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    admin: User,
    auth_headers: AuthHeaders,
) -> None:
    mine = await PaymentFactory.create_async(booking_id=(await book(offering, user)).id)
    stranger = await UserFactory.create_async()
    theirs = await PaymentFactory.create_async(
        booking_id=(await book(offering, stranger, appointment_at=in_days(4))).id
    )

    own_list = await client.get(PAYMENTS, headers=auth_headers(user))
    peek = await client.get(f"{PAYMENTS}{theirs.id}/", headers=auth_headers(user))
    own = await client.get(f"{PAYMENTS}{mine.id}/", headers=auth_headers(user))
    admin_list = await client.get(PAYMENTS, headers=auth_headers(admin))

    assert [p["id"] for p in own_list.json()["items"]] == [str(mine.id)]
    assert peek.status_code == 404
    assert own.status_code == 200
    assert admin_list.json()["total"] == 2
