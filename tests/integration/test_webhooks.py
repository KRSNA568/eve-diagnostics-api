from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from eve.cli.commands import build_payment_event, send_webhook
from eve.payments.models import Payment, PaymentStatus, WebhookEvent, WebhookEventStatus
from eve.payments.webhook import WebhookService
from eve.payments.webhook_signature import SIGNATURE_HEADER, sign
from tests.conftest import TEST_WEBHOOK_SECRET
from tests.factories import AuthHeaders, Offering, PaymentFactory, UserFactory, book

WEBHOOK = "/api/v1/payments/webhook/"


async def deliver(client: AsyncClient, event: dict[str, Any], repeat: int = 1) -> list[Response]:
    return await send_webhook(client, WEBHOOK, event, TEST_WEBHOOK_SECRET, repeat=repeat)


def event_for(booking: Booking, outcome: str = "succeeded", **overrides: Any) -> dict[str, Any]:
    return build_payment_event(
        booking_id=booking.id,
        outcome=outcome,  # type: ignore[arg-type]
        amount=overrides.pop("amount", booking.amount),
        **overrides,
    )


async def stored(session: AsyncSession, model: type[Any], **filters: Any) -> list[Any]:
    stmt = select(model).filter_by(**filters).execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def refreshed(session: AsyncSession, booking: Booking) -> Booking:
    await session.refresh(booking)
    return booking


async def count(session: AsyncSession, model: type[Any]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------------------------------------- authentication


async def test_unsigned_webhooks_are_rejected_and_not_stored(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)

    response = await client.post(WEBHOOK, json=event_for(booking))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert await count(db_session, WebhookEvent) == 0


async def test_a_user_token_is_not_a_substitute_for_the_signature(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    response = await client.post(WEBHOOK, json=event_for(booking), headers=auth_headers(user))

    assert response.status_code == 401


async def test_body_tampered_after_signing_is_rejected(client: AsyncClient) -> None:
    signed = b'{"event_id":"evt_1"}'

    response = await client.post(
        WEBHOOK,
        content=b'{"event_id":"evt_2"}',
        headers={
            SIGNATURE_HEADER: sign(signed, TEST_WEBHOOK_SECRET),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401


async def test_signed_but_malformed_envelope_is_rejected(client: AsyncClient) -> None:
    body = b'{"type":"payment.succeeded","data":{}}'  # no event_id / created_at

    response = await client.post(
        WEBHOOK,
        content=body,
        headers={
            SIGNATURE_HEADER: sign(body, TEST_WEBHOOK_SECRET),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------- happy paths


async def test_success_event_confirms_the_booking(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    event = event_for(booking, provider_reference="mock_pay_provider_1")

    [response] = await deliver(client, event)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "event_id": event["event_id"]}
    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert (row.status, row.outcome, row.attempts) == (WebhookEventStatus.PROCESSED, "applied", 1)
    [payment] = await stored(db_session, Payment, booking_id=booking.id)
    assert (payment.status, payment.provider_reference) == (
        PaymentStatus.SUCCESS,
        "mock_pay_provider_1",
    )
    assert row.payment_id == payment.id
    assert (await refreshed(db_session, booking)).status is BookingStatus.CONFIRMED


async def test_failure_event_fails_the_booking(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)

    await deliver(client, event_for(booking, "failed"))

    [payment] = await stored(db_session, Payment, booking_id=booking.id)
    assert (payment.status, payment.failure_reason) == (PaymentStatus.FAILED, "card_declined")
    assert (await refreshed(db_session, booking)).status is BookingStatus.FAILED


async def test_event_completes_a_payment_we_already_know_about(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    pending = await PaymentFactory.create_async(booking_id=booking.id, amount=booking.amount)

    await deliver(client, event_for(booking, provider_reference=pending.provider_reference))

    [payment] = await stored(db_session, Payment, booking_id=booking.id)
    assert payment.id == pending.id
    assert payment.status is PaymentStatus.SUCCESS


async def test_webhook_confirming_a_payment_made_through_the_api_is_a_no_op(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    """The client's POST /payments/ and the provider's webhook both report the same payment:
    they must converge, not double-apply."""
    booking = await book(offering, user)
    paid = await client.post(
        "/api/v1/payments/",
        json={"booking_id": str(booking.id), "payment_method": "mock_card_success"},
        headers=auth_headers(user),
    )
    event = event_for(booking, provider_reference=paid.json()["provider_reference"])

    await deliver(client, event)

    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert (row.status, row.outcome) == (WebhookEventStatus.PROCESSED, "already_applied")
    assert await count(db_session, Payment) == 1


# --------------------------------------------------------------------------- idempotency


async def test_redelivered_event_is_stored_and_applied_exactly_once(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    event = event_for(booking)

    responses = await deliver(client, event, repeat=3)

    assert [r.status_code for r in responses] == [202, 200, 200]
    assert [r.json()["status"] for r in responses] == ["accepted", "duplicate", "duplicate"]
    assert await count(db_session, WebhookEvent) == 1
    assert await count(db_session, Payment) == 1
    assert (await refreshed(db_session, booking)).status is BookingStatus.CONFIRMED


async def test_a_new_event_for_an_already_applied_result_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    first = event_for(booking, provider_reference="mock_pay_same")
    second = event_for(booking, provider_reference="mock_pay_same")  # new event_id

    await deliver(client, first)
    await deliver(client, second)

    [row] = await stored(db_session, WebhookEvent, event_id=second["event_id"])
    assert (row.status, row.outcome) == (WebhookEventStatus.PROCESSED, "already_applied")
    assert await count(db_session, Payment) == 1


async def test_failure_arriving_after_success_never_reverts_the_booking(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    await deliver(client, event_for(booking, provider_reference="mock_pay_x"))
    late_failure = event_for(booking, "failed", provider_reference="mock_pay_x")

    await deliver(client, late_failure)

    [row] = await stored(db_session, WebhookEvent, event_id=late_failure["event_id"])
    assert (row.status, row.outcome) == (WebhookEventStatus.IGNORED, "conflicting_status")
    [payment] = await stored(db_session, Payment, booking_id=booking.id)
    assert payment.status is PaymentStatus.SUCCESS
    assert (await refreshed(db_session, booking)).status is BookingStatus.CONFIRMED


async def test_processing_a_finished_event_again_is_a_no_op(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    event = event_for(booking)
    await deliver(client, event)
    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])

    status = await WebhookService(db_session).process(row.id)

    [again] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert status is WebhookEventStatus.PROCESSED
    assert again.attempts == 1


# --------------------------------------------------------------------------- closed bookings


async def test_success_for_a_cancelled_booking_is_recorded_and_flagged_for_refund(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user, status=BookingStatus.CANCELLED)
    event = event_for(booking)

    await deliver(client, event)

    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert (row.status, row.outcome) == (WebhookEventStatus.PROCESSED, "refund_required")
    [payment] = await stored(db_session, Payment, booking_id=booking.id)
    assert payment.status is PaymentStatus.SUCCESS  # the money moved; record it truthfully
    assert (await refreshed(db_session, booking)).status is BookingStatus.CANCELLED


async def test_second_successful_charge_for_a_paid_booking_is_not_recorded_as_success(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    await deliver(client, event_for(booking, provider_reference="mock_pay_first"))
    second_charge = event_for(booking, provider_reference="mock_pay_second")

    await deliver(client, second_charge)

    [row] = await stored(db_session, WebhookEvent, event_id=second_charge["event_id"])
    assert (row.status, row.outcome) == (
        WebhookEventStatus.IGNORED,
        "duplicate_charge_refund_required",
    )
    assert [p.provider_reference for p in await stored(db_session, Payment)] == ["mock_pay_first"]


async def test_failure_for_a_closed_booking_is_recorded_without_touching_it(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user, status=BookingStatus.CANCELLED)
    event = event_for(booking, "failed")

    await deliver(client, event)

    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert row.outcome == "recorded_booking_closed"
    assert (await refreshed(db_session, booking)).status is BookingStatus.CANCELLED


# --------------------------------------------------------------------------- rejected events


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        pytest.param(
            lambda e, _: e["data"].update(booking_id="01a0dcae-0000-7000-8000-000000000000"),
            "unknown_booking",
            id="unknown-booking",
        ),
        pytest.param(
            lambda e, _: e["data"].update(amount="1.00"), "amount_mismatch", id="amount-mismatch"
        ),
        pytest.param(
            lambda e, _: e["data"].update(currency="USD"), "amount_mismatch", id="currency-mismatch"
        ),
        pytest.param(
            lambda e, _: e["data"].pop("provider_reference"),
            "invalid_payload",
            id="missing-reference",
        ),
        pytest.param(
            lambda e, _: e["data"].update(amount="not-a-number"),
            "invalid_payload",
            id="invalid-amount",
        ),
    ],
)
async def test_events_that_cannot_be_applied_are_kept_as_failed(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    mutate: Any,
    reason: str,
) -> None:
    booking = await book(offering, user)
    event = event_for(booking)
    mutate(event, booking)

    [response] = await deliver(client, event)

    assert response.status_code == 202  # stored for investigation; retrying would not help
    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert (row.status, row.last_error) == (WebhookEventStatus.FAILED, reason)
    assert await count(db_session, Payment) == 0
    assert (await refreshed(db_session, booking)).status is BookingStatus.PENDING


async def test_reference_belonging_to_another_booking_is_rejected(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    mine = await book(offering, user)
    other = await book(offering, await UserFactory.create_async())
    theirs = await PaymentFactory.create_async(booking_id=other.id, amount=other.amount)
    event = event_for(mine, provider_reference=theirs.provider_reference)

    await deliver(client, event)

    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert (row.status, row.last_error) == (WebhookEventStatus.FAILED, "booking_mismatch")


async def test_unsupported_event_types_are_acknowledged_and_ignored(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user)
    event = event_for(booking) | {"type": "payment.refunded", "extra_field": {"ignored": True}}

    [response] = await deliver(client, event)

    assert response.status_code == 202
    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert (row.status, row.outcome) == (WebhookEventStatus.IGNORED, "unsupported_event_type")
    assert row.payload["extra_field"] == {"ignored": True}  # raw payload kept for audit


async def test_webhook_amounts_are_compared_as_decimals(
    client: AsyncClient, db_session: AsyncSession, offering: Offering, user: User
) -> None:
    booking = await book(offering, user, amount=Decimal("349.00"))

    await deliver(client, event_for(booking, amount=Decimal("349")))

    assert (await refreshed(db_session, booking)).status is BookingStatus.CONFIRMED


async def test_event_is_kept_for_retry_when_processing_crashes(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def crash(self: WebhookService, event_pk: Any) -> None:
        raise ConnectionError("database went away")

    monkeypatch.setattr(WebhookService, "process", crash)
    booking = await book(offering, user)
    event = event_for(booking)

    [response] = await deliver(client, event)

    # Acknowledged: the event is stored durably, so the provider must not resend it.
    assert response.status_code == 202
    [row] = await stored(db_session, WebhookEvent, event_id=event["event_id"])
    assert row.status is WebhookEventStatus.RECEIVED
