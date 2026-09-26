from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus
from tests.factories import (
    AuthHeaders,
    BookingFactory,
    DiagnosticTestFactory,
    Offering,
    UserFactory,
    book,
    in_days,
)

BOOKINGS = "/api/v1/bookings/"


# --------------------------------------------------------------------------- create


async def test_booking_is_created_pending_at_the_catalog_price(
    client: AsyncClient, offering: Offering, user: User, auth_headers: AuthHeaders
) -> None:
    response = await client.post(
        BOOKINGS, json=offering.booking_payload(), headers=auth_headers(user)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert (body["amount"], body["currency"]) == ("349.00", "INR")
    assert body["centre"] == {
        "id": str(offering.centre.id),
        "name": "CareLab Andheri",
        "city": "Mumbai",
    }
    assert body["test"]["code"] == "CBC"
    assert body["appointment_at"].endswith("Z")


async def test_appointment_in_any_offset_is_stored_and_returned_in_utc(
    client: AsyncClient, offering: Offering, user: User, auth_headers: AuthHeaders
) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    local = in_days(3).astimezone(ist)

    response = await client.post(
        BOOKINGS,
        json=offering.booking_payload(appointment_at=local.isoformat()),
        headers=auth_headers(user),
    )

    returned = response.json()["appointment_at"]
    assert returned.endswith("Z")
    assert datetime.fromisoformat(returned) == local  # same instant, different offset


async def test_booking_keeps_its_price_when_the_catalog_price_changes(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    created = await client.post(
        BOOKINGS, json=offering.booking_payload(), headers=auth_headers(user)
    )
    offering.row.price = Decimal("999.00")
    await db_session.commit()

    fetched = await client.get(f"{BOOKINGS}{created.json()['id']}/", headers=auth_headers(user))

    assert fetched.json()["amount"] == "349.00"


async def test_booking_requires_authentication(client: AsyncClient, offering: Offering) -> None:
    response = await client.post(BOOKINGS, json=offering.booking_payload())

    assert response.status_code == 401


@pytest.mark.parametrize("field", ["amount", "status", "user_id"])
async def test_client_cannot_set_server_owned_fields(
    client: AsyncClient, offering: Offering, user: User, auth_headers: AuthHeaders, field: str
) -> None:
    payload = offering.booking_payload(**{field: "1.00"})

    response = await client.post(BOOKINGS, json=payload, headers=auth_headers(user))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "appointment_at",
    [
        pytest.param(in_days(-1).isoformat(), id="in-the-past"),
        pytest.param(in_days(0.01).isoformat(), id="inside-minimum-lead-time"),
        pytest.param(in_days(61).isoformat(), id="too-far-ahead"),
    ],
)
async def test_appointment_must_be_inside_the_bookable_window(
    client: AsyncClient,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
    appointment_at: str,
) -> None:
    response = await client.post(
        BOOKINGS,
        json=offering.booking_payload(appointment_at=appointment_at),
        headers=auth_headers(user),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_APPOINTMENT_TIME"


async def test_appointment_without_timezone_is_rejected(
    client: AsyncClient, offering: Offering, user: User, auth_headers: AuthHeaders
) -> None:
    naive = in_days(3).replace(tzinfo=None).isoformat()

    response = await client.post(
        BOOKINGS, json=offering.booking_payload(appointment_at=naive), headers=auth_headers(user)
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["errors"][0]["field"] == "appointment_at"


@pytest.mark.parametrize(
    "make_unbookable",
    [
        pytest.param(lambda o: setattr(o.row, "is_available", False), id="offering-paused"),
        pytest.param(lambda o: setattr(o.centre, "is_active", False), id="centre-closed"),
        pytest.param(lambda o: setattr(o.test, "is_active", False), id="test-retired"),
    ],
)
async def test_unbookable_offerings_are_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
    make_unbookable: Any,
) -> None:
    make_unbookable(offering)
    await db_session.commit()

    response = await client.post(
        BOOKINGS, json=offering.booking_payload(), headers=auth_headers(user)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OFFERING_UNAVAILABLE"


async def test_test_not_offered_at_the_centre_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    other_test = await DiagnosticTestFactory.create_async()

    response = await client.post(
        BOOKINGS,
        json=offering.booking_payload(test_id=str(other_test.id)),
        headers=auth_headers(user),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OFFERING_UNAVAILABLE"


async def test_duplicate_active_booking_is_rejected_but_rebooking_after_cancel_works(
    client: AsyncClient, offering: Offering, user: User, auth_headers: AuthHeaders
) -> None:
    payload = offering.booking_payload()
    headers = auth_headers(user)

    first = await client.post(BOOKINGS, json=payload, headers=headers)
    duplicate = await client.post(BOOKINGS, json=payload, headers=headers)
    await client.post(f"{BOOKINGS}{first.json()['id']}/cancel/", headers=headers)
    rebooked = await client.post(BOOKINGS, json=payload, headers=headers)

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_BOOKING"
    assert rebooked.status_code == 201


async def test_database_refuses_a_booking_for_a_test_the_centre_does_not_offer(
    db_session: AsyncSession, offering: Offering, user: User
) -> None:
    other_test = await DiagnosticTestFactory.create_async()

    with pytest.raises(IntegrityError, match="fk_bookings_offering"):
        await BookingFactory.create_async(
            user_id=user.id, centre_id=offering.centre.id, test_id=other_test.id
        )


# --------------------------------------------------------------------------- read


async def test_users_only_see_their_own_bookings(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    mine = await book(offering, user)
    stranger = await UserFactory.create_async()
    theirs = await book(offering, stranger, appointment_at=in_days(4))

    listing = await client.get(BOOKINGS, headers=auth_headers(user))
    peek = await client.get(f"{BOOKINGS}{theirs.id}/", headers=auth_headers(user))

    assert [b["id"] for b in listing.json()["items"]] == [str(mine.id)]
    assert peek.status_code == 404
    assert peek.json()["error"]["code"] == "BOOKING_NOT_FOUND"


async def test_admins_see_every_booking(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    admin: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)

    listing = await client.get(BOOKINGS, headers=auth_headers(admin))
    detail = await client.get(f"{BOOKINGS}{booking.id}/", headers=auth_headers(admin))

    assert listing.json()["total"] == 1
    assert detail.status_code == 200


async def test_bookings_can_be_filtered_by_status_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    older = await book(offering, user, appointment_at=in_days(2))
    newer = await book(offering, user, appointment_at=in_days(5))
    await book(offering, user, appointment_at=in_days(6), status=BookingStatus.CANCELLED)

    response = await client.get(BOOKINGS, params={"status": "PENDING"}, headers=auth_headers(user))

    assert [b["id"] for b in response.json()["items"]] == [str(newer.id), str(older.id)]


async def test_unknown_status_filter_is_rejected(
    client: AsyncClient, user: User, auth_headers: AuthHeaders
) -> None:
    response = await client.get(BOOKINGS, params={"status": "PAID"}, headers=auth_headers(user))

    assert response.status_code == 422


async def test_unknown_booking_returns_404(
    client: AsyncClient, user: User, auth_headers: AuthHeaders
) -> None:
    response = await client.get(
        f"{BOOKINGS}01a0dcae-0000-7000-8000-000000000000/", headers=auth_headers(user)
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------- cancel


async def test_cancel_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user)
    url = f"{BOOKINGS}{booking.id}/cancel/"

    first = await client.post(url, headers=auth_headers(user))
    second = await client.post(url, headers=auth_headers(user))

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "CANCELLED"
    assert first.json()["status_reason"] == "cancelled_by_user"
    assert second.json()["cancelled_at"] == first.json()["cancelled_at"]


async def test_confirmed_booking_can_be_cancelled(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user, status=BookingStatus.CONFIRMED)

    response = await client.post(f"{BOOKINGS}{booking.id}/cancel/", headers=auth_headers(user))

    assert response.json()["status"] == "CANCELLED"


async def test_failed_booking_cannot_be_cancelled(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    booking = await book(offering, user, status=BookingStatus.FAILED)

    response = await client.post(f"{BOOKINGS}{booking.id}/cancel/", headers=auth_headers(user))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


async def test_cannot_cancel_after_the_appointment(
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
        .values(appointment_at=in_days(0) - timedelta(hours=1))
    )
    await db_session.commit()

    response = await client.post(f"{BOOKINGS}{booking.id}/cancel/", headers=auth_headers(user))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPOINTMENT_PASSED"


async def test_cannot_cancel_someone_elses_booking(
    client: AsyncClient,
    db_session: AsyncSession,
    offering: Offering,
    user: User,
    auth_headers: AuthHeaders,
) -> None:
    stranger = await UserFactory.create_async()
    booking = await book(offering, stranger)

    response = await client.post(f"{BOOKINGS}{booking.id}/cancel/", headers=auth_headers(user))

    assert response.status_code == 404
