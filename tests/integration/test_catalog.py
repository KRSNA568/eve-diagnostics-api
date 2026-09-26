from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from tests.factories import (
    AuthHeaders,
    CentreFactory,
    DiagnosticTestFactory,
    OfferingFactory,
)

CENTRES = "/api/v1/centres/"
TESTS = "/api/v1/tests/"

NEW_CENTRE: dict[str, Any] = {
    "name": "CareLab Diagnostics - Andheri",
    "address": "Link Road, Andheri West",
    "city": "Mumbai",
    "pincode": "400053",
}


async def offer(
    centre: DiagnosticCentre, test: DiagnosticTest, price: str = "499.00", **kwargs: Any
) -> CentreTest:
    return await OfferingFactory.create_async(
        centre_id=centre.id, test_id=test.id, price=Decimal(price), **kwargs
    )


# --------------------------------------------------------------------------- public reads


async def test_centres_are_listed_with_pagination_metadata(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await CentreFactory.create_batch_async(3)

    response = await client.get(CENTRES, params={"page": 2, "size": 2})

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"], body["size"], body["pages"]) == (3, 2, 2, 2)
    assert len(body["items"]) == 1


async def test_pages_are_ordered_deterministically(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    for name in ["Charlie Labs", "Alpha Labs", "Bravo Labs"]:
        await CentreFactory.create_async(name=name)

    first = (await client.get(CENTRES, params={"size": 2})).json()["items"]
    second = (await client.get(CENTRES, params={"size": 2, "page": 2})).json()["items"]

    assert [c["name"] for c in first + second] == ["Alpha Labs", "Bravo Labs", "Charlie Labs"]


@pytest.mark.parametrize("params", [{"page": 0}, {"size": 0}, {"size": 101}])
async def test_invalid_pagination_is_rejected(client: AsyncClient, params: dict[str, int]) -> None:
    response = await client.get(CENTRES, params=params)

    assert response.status_code == 422


async def test_city_filter_is_case_insensitive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await CentreFactory.create_async(city="Mumbai")
    await CentreFactory.create_async(city="Pune")

    response = await client.get(CENTRES, params={"city": "mUMBAI"})

    assert [c["city"] for c in response.json()["items"]] == ["Mumbai"]


async def test_test_filter_returns_only_centres_currently_offering_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    cbc = await DiagnosticTestFactory.create_async(code="CBC")
    offers_it, paused, _does_not_offer = await CentreFactory.create_batch_async(3)
    await offer(offers_it, cbc)
    await offer(paused, cbc, is_available=False)

    response = await client.get(CENTRES, params={"test_id": str(cbc.id)})

    assert [c["id"] for c in response.json()["items"]] == [str(offers_it.id)]


async def test_name_search_treats_like_wildcards_literally(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await CentreFactory.create_async(name="100% Pure Labs")
    await CentreFactory.create_async(name="1000 Labs")

    response = await client.get(CENTRES, params={"q": "100%"})

    assert [c["name"] for c in response.json()["items"]] == ["100% Pure Labs"]


async def test_inactive_centres_are_hidden(client: AsyncClient, db_session: AsyncSession) -> None:
    closed = await CentreFactory.create_async(is_active=False)

    listing = await client.get(CENTRES)
    detail = await client.get(f"{CENTRES}{closed.id}/")

    assert listing.json()["total"] == 0
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "CENTRE_NOT_FOUND"


async def test_centre_detail_lists_bookable_tests_with_prices(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    centre = await CentreFactory.create_async()
    cbc = await DiagnosticTestFactory.create_async(code="CBC", name="Complete Blood Count")
    lipid = await DiagnosticTestFactory.create_async(code="LIPID", name="Lipid Profile")
    retired = await DiagnosticTestFactory.create_async(is_active=False)
    paused = await DiagnosticTestFactory.create_async()
    await offer(centre, cbc, "349.00")
    await offer(centre, lipid, "699.50")
    await offer(centre, retired)
    await offer(centre, paused, is_available=False)

    response = await client.get(f"{CENTRES}{centre.id}/")

    assert response.status_code == 200
    offerings = response.json()["offerings"]
    assert [(o["test"]["code"], o["price"], o["currency"]) for o in offerings] == [
        ("CBC", "349.00", "INR"),
        ("LIPID", "699.50", "INR"),
    ]


async def test_malformed_ids_are_rejected(client: AsyncClient) -> None:
    response = await client.get(f"{CENTRES}not-a-uuid/")

    assert response.status_code == 422
    assert response.json()["error"]["details"]["errors"][0]["location"] == "path"


async def test_tests_are_searchable_by_name_or_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await DiagnosticTestFactory.create_async(code="TSH", name="Thyroid Stimulating Hormone")
    await DiagnosticTestFactory.create_async(code="CBC", name="Complete Blood Count")
    await DiagnosticTestFactory.create_async(code="OLD", name="Thyroid legacy", is_active=False)

    by_name = await client.get(TESTS, params={"q": "thyroid"})
    by_code = await client.get(TESTS, params={"q": "cbc"})

    assert [t["code"] for t in by_name.json()["items"]] == ["TSH"]
    assert [t["code"] for t in by_code.json()["items"]] == ["CBC"]


async def test_single_test_lookup(client: AsyncClient, db_session: AsyncSession) -> None:
    test = await DiagnosticTestFactory.create_async(code="VITD")
    retired = await DiagnosticTestFactory.create_async(is_active=False)

    found = await client.get(f"{TESTS}{test.id}/")
    missing = await client.get(f"{TESTS}{retired.id}/")

    assert found.json()["code"] == "VITD"
    assert missing.status_code == 404


# --------------------------------------------------------------------------- admin writes


async def test_catalog_writes_require_authentication(client: AsyncClient) -> None:
    response = await client.post(CENTRES, json=NEW_CENTRE)

    assert response.status_code == 401


async def test_catalog_writes_require_admin(
    client: AsyncClient, user: User, auth_headers: AuthHeaders
) -> None:
    response = await client.post(CENTRES, json=NEW_CENTRE, headers=auth_headers(user))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_admin_creates_updates_and_deactivates_a_centre(
    client: AsyncClient, admin: User, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers(admin)

    created = await client.post(CENTRES, json=NEW_CENTRE, headers=headers)
    assert created.status_code == 201
    centre_id = created.json()["id"]

    updated = await client.patch(
        f"{CENTRES}{centre_id}/", json={"address": "New Link Road"}, headers=headers
    )
    assert updated.json()["address"] == "New Link Road"
    assert updated.json()["name"] == NEW_CENTRE["name"]

    deleted = await client.delete(f"{CENTRES}{centre_id}/", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get(f"{CENTRES}{centre_id}/")).status_code == 404

    reactivated = await client.patch(
        f"{CENTRES}{centre_id}/", json={"is_active": True}, headers=headers
    )
    assert reactivated.json()["is_active"] is True


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"name": None}, ""),
        ({"pincode": "012345"}, "pincode"),
        ({"pincode": "40005"}, "pincode"),
        ({"unknown": "x"}, "unknown"),
    ],
)
async def test_centre_update_validates_input(
    client: AsyncClient,
    db_session: AsyncSession,
    admin: User,
    auth_headers: AuthHeaders,
    payload: dict[str, Any],
    field: str,
) -> None:
    centre = await CentreFactory.create_async()

    response = await client.patch(
        f"{CENTRES}{centre.id}/", json=payload, headers=auth_headers(admin)
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["errors"][0]["field"] == field


async def test_updating_an_unknown_centre_returns_404(
    client: AsyncClient, admin: User, auth_headers: AuthHeaders
) -> None:
    response = await client.patch(
        f"{CENTRES}01a0dcae-0000-7000-8000-000000000000/",
        json={"name": "x"},
        headers=auth_headers(admin),
    )

    assert response.status_code == 404


async def test_test_codes_are_unique_case_insensitively(
    client: AsyncClient, admin: User, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers(admin)
    first = await client.post(TESTS, json={"code": "cbc", "name": "CBC"}, headers=headers)
    second = await client.post(TESTS, json={"code": "CBC", "name": "CBC again"}, headers=headers)

    assert first.status_code == 201
    assert first.json()["code"] == "CBC"
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TEST_CODE_TAKEN"


async def test_test_description_can_be_cleared_but_name_cannot(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    test = await DiagnosticTestFactory.create_async(description="old")
    url = f"{TESTS}{test.id}/"

    cleared = await client.patch(url, json={"description": None}, headers=auth_headers(admin))
    rejected = await client.patch(url, json={"name": None}, headers=auth_headers(admin))

    assert cleared.status_code == 200
    assert cleared.json()["description"] is None
    assert rejected.status_code == 422


async def test_admin_offers_a_test_and_changes_its_price(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    centre = await CentreFactory.create_async()
    test = await DiagnosticTestFactory.create_async(code="HBA1C")
    headers = auth_headers(admin)

    created = await client.post(
        f"{CENTRES}{centre.id}/tests/",
        json={"test_id": str(test.id), "price": "499.00"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["test"]["code"] == "HBA1C"
    assert created.json()["price"] == "499.00"

    repriced = await client.patch(
        f"{CENTRES}{centre.id}/tests/{test.id}/", json={"price": "549.00"}, headers=headers
    )
    assert repriced.json()["price"] == "549.00"


async def test_a_centre_cannot_offer_the_same_test_twice(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    centre = await CentreFactory.create_async()
    test = await DiagnosticTestFactory.create_async()
    await offer(centre, test)

    response = await client.post(
        f"{CENTRES}{centre.id}/tests/",
        json={"test_id": str(test.id), "price": "100.00"},
        headers=auth_headers(admin),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OFFERING_EXISTS"


@pytest.mark.parametrize("price", ["0", "-10.00", "10.999", "abc", "100000000.00"])
async def test_offering_price_is_validated(
    client: AsyncClient,
    db_session: AsyncSession,
    admin: User,
    auth_headers: AuthHeaders,
    price: str,
) -> None:
    centre = await CentreFactory.create_async()
    test = await DiagnosticTestFactory.create_async()

    response = await client.post(
        f"{CENTRES}{centre.id}/tests/",
        json={"test_id": str(test.id), "price": price},
        headers=auth_headers(admin),
    )

    assert response.status_code == 422


async def test_offering_references_are_checked(
    client: AsyncClient, db_session: AsyncSession, admin: User, auth_headers: AuthHeaders
) -> None:
    centre = await CentreFactory.create_async()
    test = await DiagnosticTestFactory.create_async()
    unknown = "01a0dcae-0000-7000-8000-000000000000"
    headers = auth_headers(admin)

    no_test = await client.post(
        f"{CENTRES}{centre.id}/tests/", json={"test_id": unknown, "price": "1.00"}, headers=headers
    )
    no_centre = await client.post(
        f"{CENTRES}{unknown}/tests/",
        json={"test_id": str(test.id), "price": "1.00"},
        headers=headers,
    )
    no_offering = await client.patch(
        f"{CENTRES}{centre.id}/tests/{test.id}/", json={"price": "1.00"}, headers=headers
    )

    assert (no_test.status_code, no_test.json()["error"]["code"]) == (422, "TEST_NOT_FOUND")
    assert (no_centre.status_code, no_centre.json()["error"]["code"]) == (404, "CENTRE_NOT_FOUND")
    assert (no_offering.status_code, no_offering.json()["error"]["code"]) == (
        404,
        "OFFERING_NOT_FOUND",
    )
