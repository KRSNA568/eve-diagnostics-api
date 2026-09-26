from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from eve.bookings.state import BookingStatus
from eve.payments.gateway import MockPaymentMethod
from eve.payments.models import PaymentStatus


class PaymentCreate(BaseModel):
    # No amount: the booking's snapshotted price is charged, whatever the client says.
    model_config = ConfigDict(extra="forbid")

    booking_id: UUID
    payment_method: MockPaymentMethod = Field(
        default=MockPaymentMethod.RANDOM,
        description=(
            "Simulated payment method: `mock_card_success` always succeeds, "
            "`mock_card_declined` always fails, `mock_card_random` succeeds with the "
            "configured probability."
        ),
    )


class BookingStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: BookingStatus


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: PaymentStatus
    amount: Decimal
    currency: str
    provider: str
    provider_reference: str
    failure_reason: str | None
    booking: BookingStatusRead
    created_at: datetime
    completed_at: datetime | None


# --------------------------------------------------------------------------- webhooks


class WebhookEventType(StrEnum):
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"


class WebhookEventIn(BaseModel):
    """Provider event envelope.

    Unknown fields are ignored and `type` is free-form: providers add fields and event
    types over time, and rejecting them would only make the provider retry forever.
    Unsupported types are stored and acknowledged, then marked IGNORED.
    """

    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    type: str = Field(min_length=1, max_length=100, examples=["payment.succeeded"])
    created_at: AwareDatetime
    data: dict[str, Any]


class WebhookPaymentData(BaseModel):
    """`data` of payment.* events; validated when the event is processed."""

    model_config = ConfigDict(extra="ignore")

    provider_reference: str = Field(min_length=1, max_length=64)
    booking_id: UUID
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    failure_reason: str | None = Field(default=None, max_length=200)


class WebhookAck(BaseModel):
    status: Literal["accepted", "duplicate"]
    event_id: str
