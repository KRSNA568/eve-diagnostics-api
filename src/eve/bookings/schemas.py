from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from eve.bookings.state import BookingStatus

# Accept any UTC offset from clients, but normalise at the boundary so everything past this
# point - service, database, response - only ever sees UTC.
UTCInstant = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]


class BookingCreate(BaseModel):
    # No amount or status: the server derives both, so a client cannot choose its price.
    model_config = ConfigDict(extra="forbid")

    centre_id: UUID
    test_id: UUID
    appointment_at: UTCInstant = Field(
        description="ISO 8601 with a UTC offset, e.g. 2026-10-01T09:30:00+05:30",
        examples=["2026-10-01T09:30:00+05:30"],
    )


class CentreSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    city: str


class TestSummary(BaseModel):
    __test__ = False  # not a pytest test class

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: BookingStatus
    status_reason: str | None
    appointment_at: datetime
    amount: Decimal
    currency: str
    centre: CentreSummary
    test: TestSummary
    created_at: datetime
    confirmed_at: datetime | None
    cancelled_at: datetime | None
