"""Correctness under concurrency, against real Postgres.

Requests are fired together with asyncio.gather; each runs on its own pooled connection, so
row locks (SELECT ... FOR UPDATE), unique indexes and ON CONFLICT are genuinely contended.
These tests would fail with check-then-act logic in application code.
"""

import asyncio
from collections import Counter
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from eve.cli.commands import build_payment_event, send_webhook
from eve.payments.gateway import ChargeRequest, ChargeResult, MockPaymentGateway
from eve.payments.models import Payment, PaymentStatus, WebhookEvent, WebhookEventStatus
from eve.payments.router import get_payment_gateway
from tests.conftest import TEST_WEBHOOK_SECRET
from tests.factories import AuthHeaders, Offering, book
from tests.integration.conftest import RunWorker

WEBHOOK = "/api/v1/payments/webhook/"


async def count(session: AsyncSession, model: type[Any], *where: Any) -> int:
    stmt = select(func.count()).select_from(model).where(*where)
    return (await session.execute(stmt)).scalar_one()


class CountingGateway(MockPaymentGateway):
    def __init__(self) -> None:
        super().__init__(success_rate=1)
        self.charges = 0

    async def charge(self, request: ChargeRequest) -> ChargeResult:
        self.charges += 1
        return await super().charge(request)


async def test_parallel_payments_for_one_booking_charge_exactly_once(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    gateway = CountingGateway()
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    booking = await book(offering, user)
    body = {"booking_id": str(booking.id), "payment_method": "mock_card_success"}

    responses = await asyncio.gather(
        *(client.post("/api/v1/payments/", json=body, headers=auth_headers(user)) for _ in range(8))
    )

    # The booking row lock is what stops duplicate charges reaching the provider; the
    # one-SUCCESS-per-booking index only stops them being recorded.
    assert gateway.charges == 1
    assert Counter(r.status_code for r in responses) == {201: 1, 409: 7}
    assert await count(db_session, Payment, Payment.booking_id == booking.id) == 1
    await db_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED


async def test_parallel_deliveries_of_one_webhook_are_stored_and_applied_once(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    run_worker: RunWorker,
) -> None:
    booking = await book(offering, user)
    event = build_payment_event(booking_id=booking.id, outcome="succeeded", amount=booking.amount)

    deliveries = await asyncio.gather(
        *(send_webhook(client, WEBHOOK, event, TEST_WEBHOOK_SECRET) for _ in range(10))
    )
    await run_worker()

    statuses = Counter(r.json()["status"] for [r] in deliveries)
    assert statuses == {"accepted": 1, "duplicate": 9}
    assert await count(db_session, WebhookEvent) == 1
    assert await count(db_session, Payment) == 1
    await db_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED


async def test_concurrent_events_for_the_same_payment_apply_it_once(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    run_worker: RunWorker,
) -> None:
    """Distinct events (different event_ids) about one payment, processed by concurrent
    worker jobs: the booking lock serialises them, so exactly one applies it."""
    booking = await book(offering, user)
    events = [
        build_payment_event(
            booking_id=booking.id,
            outcome="succeeded",
            amount=booking.amount,
            provider_reference="mock_pay_contended",
        )
        for _ in range(5)
    ]
    for event in events:
        await send_webhook(client, WEBHOOK, event, TEST_WEBHOOK_SECRET)

    await run_worker()  # ARQ runs the five jobs concurrently

    outcomes = Counter((await db_session.execute(select(WebhookEvent.outcome))).scalars().all())
    assert outcomes == {"applied": 1, "already_applied": 4}
    assert await count(db_session, Payment) == 1
    assert (
        await count(db_session, WebhookEvent, WebhookEvent.status != WebhookEventStatus.PROCESSED)
        == 0
    )


async def test_double_clicked_booking_creates_one_booking(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    payload = offering.booking_payload()

    responses = await asyncio.gather(
        *(
            client.post("/api/v1/bookings/", json=payload, headers=auth_headers(user))
            for _ in range(5)
        )
    )

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 4}
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 409} == {
        "DUPLICATE_BOOKING"
    }
    assert await count(db_session, Booking) == 1


async def test_parallel_signups_with_one_email_create_one_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    body = {"email": "race@example.com", "password": "Password123", "full_name": "Racer"}

    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/signup/", json=body) for _ in range(5))
    )

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 4}
    assert await count(db_session, User, User.email == "race@example.com") == 1


async def test_payment_racing_a_cancellation_leaves_a_consistent_state(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    pay, cancel = await asyncio.gather(
        client.post(
            "/api/v1/payments/",
            json={"booking_id": str(booking.id), "payment_method": "mock_card_success"},
            headers=auth_headers(user),
        ),
        client.post(f"/api/v1/bookings/{booking.id}/cancel/", headers=auth_headers(user)),
    )

    await db_session.refresh(booking)
    assert cancel.status_code == 200
    assert booking.status is BookingStatus.CANCELLED  # cancel is valid from PENDING or CONFIRMED
    successful = await count(
        db_session,
        Payment,
        Payment.booking_id == booking.id,
        Payment.status == PaymentStatus.SUCCESS,
    )
    if pay.status_code == 201:  # payment won the lock: it was charged, then cancelled
        assert successful == 1
    else:  # cancellation won: the payment was refused before any charge
        assert pay.json()["error"]["code"] == "BOOKING_NOT_PAYABLE"
        assert successful == 0
