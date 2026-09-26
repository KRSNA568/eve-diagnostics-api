"""Operational commands. Kept free of argparse so tests can call them directly."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from eve.auth.models import User
from eve.auth.repository import UserRepository
from eve.auth.schemas import SignupRequest
from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.core.security import hash_password
from eve.payments.webhook_signature import SIGNATURE_HEADER, sign

# Fictional demo data: no real diagnostic chain is represented.
DEMO_TESTS: list[tuple[str, str, str]] = [
    ("CBC", "Complete Blood Count", "Haemoglobin, RBC, WBC and platelet counts."),
    ("LIPID", "Lipid Profile", "Total cholesterol, HDL, LDL and triglycerides."),
    ("HBA1C", "Glycated Haemoglobin (HbA1c)", "Average blood sugar over ~3 months."),
    ("TSH", "Thyroid Stimulating Hormone", "Screens thyroid function."),
    ("VITD", "Vitamin D (25-OH)", "Vitamin D level in blood."),
    ("LFT", "Liver Function Test", "Enzymes and proteins produced by the liver."),
]

DEMO_CENTRES: list[tuple[str, str, str, str, dict[str, str]]] = [
    (
        "CareLab Diagnostics - Andheri",
        "Shop 4, Link Road, Andheri West",
        "Mumbai",
        "400053",
        {"CBC": "349.00", "LIPID": "699.00", "HBA1C": "499.00", "TSH": "399.00"},
    ),
    (
        "CareLab Diagnostics - Koramangala",
        "80 Feet Road, Koramangala 4th Block",
        "Bengaluru",
        "560034",
        {"CBC": "299.00", "LIPID": "649.00", "VITD": "1199.00", "LFT": "749.00"},
    ),
    (
        "PulsePath Labs - Saket",
        "Press Enclave Marg, Saket",
        "New Delhi",
        "110017",
        {"CBC": "379.00", "HBA1C": "549.00", "TSH": "429.00", "VITD": "1299.00"},
    ),
    (
        "Vita Diagnostics - Baner",
        "Baner Road, near Balewadi Phata",
        "Pune",
        "411045",
        {"CBC": "279.00", "LIPID": "599.00", "LFT": "699.00"},
    ),
]


@dataclass(frozen=True, slots=True)
class SeedResult:
    tests: int
    centres: int
    offerings: int


async def seed_catalog(session: AsyncSession) -> SeedResult:
    """Insert the demo catalog. Idempotent: existing rows are left untouched."""
    await session.execute(
        insert(DiagnosticTest)
        .values([{"code": c, "name": n, "description": d} for c, n, d in DEMO_TESTS])
        .on_conflict_do_nothing(index_elements=["code"])
    )
    rows = (await session.execute(select(DiagnosticTest.code, DiagnosticTest.id))).all()
    test_ids = {row.code: row.id for row in rows}

    offerings = 0
    for name, address, city, pincode, prices in DEMO_CENTRES:
        centre = (
            await session.execute(
                select(DiagnosticCentre).where(
                    DiagnosticCentre.name == name, DiagnosticCentre.city == city
                )
            )
        ).scalar_one_or_none()
        if centre is None:
            centre = DiagnosticCentre(name=name, address=address, city=city, pincode=pincode)
            session.add(centre)
            await session.flush()

        await session.execute(
            insert(CentreTest)
            .values(
                [
                    {"centre_id": centre.id, "test_id": test_ids[code], "price": Decimal(price)}
                    for code, price in prices.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=["centre_id", "test_id"])
        )
        offerings += len(prices)

    await session.commit()
    return SeedResult(tests=len(DEMO_TESTS), centres=len(DEMO_CENTRES), offerings=offerings)


async def create_admin(session: AsyncSession, email: str, password: str) -> tuple[User, bool]:
    """Create an admin account, or promote the existing account with this email.

    Returns (user, created). Input is validated with the same rules as public signup.
    """
    data = SignupRequest.model_validate(
        {"email": email, "password": password, "full_name": "Administrator"}
    )
    users = UserRepository(session)

    user = await users.get_by_email(data.email)
    created = user is None
    if user is None:
        user = User(
            email=data.email,
            password_hash=hash_password(data.password.get_secret_value()),
            full_name=data.full_name,
            is_admin=True,
        )
        users.add(user)
    else:
        user.is_admin = True
    await session.commit()
    return user, created


# --------------------------------------------------------------------------- mock provider


def build_payment_event(
    *,
    booking_id: UUID,
    outcome: Literal["succeeded", "failed"],
    amount: Decimal,
    currency: str = "INR",
    provider_reference: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """A payment event as the (simulated) provider would send it."""
    return {
        "event_id": event_id or f"evt_{uuid7().hex}",
        "type": f"payment.{outcome}",
        "created_at": datetime.now(UTC).isoformat(),
        "data": {
            "provider_reference": provider_reference or f"mock_pay_{uuid7().hex}",
            "booking_id": str(booking_id),
            "amount": str(amount),
            "currency": currency,
            "failure_reason": "card_declined" if outcome == "failed" else None,
        },
    }


async def send_webhook(
    client: httpx.AsyncClient,
    url: str,
    event: dict[str, Any],
    secret: str,
    *,
    repeat: int = 1,
) -> list[httpx.Response]:
    """Sign and POST `event`, `repeat` times - redeliveries exercise idempotency."""
    body = json.dumps(event, separators=(",", ":")).encode()
    responses = []
    for _ in range(repeat):
        # Re-sign each delivery, as a provider retrying later would (fresh timestamp).
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(body, secret)}
        responses.append(await client.post(url, content=body, headers=headers))
    return responses
