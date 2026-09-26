from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
