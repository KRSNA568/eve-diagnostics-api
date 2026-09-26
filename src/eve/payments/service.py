from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.repository import BookingRepository
from eve.bookings.state import BookingStatus
from eve.core.errors import ConflictError, NotFoundError
from eve.core.pagination import Page, PageParams, paginate
from eve.payments.gateway import ChargeRequest, PaymentGateway
from eve.payments.models import Payment, PaymentStatus
from eve.payments.repository import PaymentRepository
from eve.payments.schemas import PaymentCreate, PaymentRead

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PaymentOutcome:
    payment: PaymentRead
    replayed: bool  # True when an Idempotency-Key matched an earlier request


def apply_payment_result(
    booking: Booking,
    payment: Payment,
    status: PaymentStatus,
    failure_reason: str | None = None,
) -> bool:
    """Record a final payment status and move the booking accordingly.

    The single place where a payment result changes state - used by the payment endpoint
    and (later) the provider webhook, so both paths behave identically. Returns False if the
    payment already had this status (a repeat, so nothing changes).
    """
    if not payment.record_result(status, failure_reason=failure_reason):
        return False
    if status is PaymentStatus.SUCCESS:
        booking.transition_to(BookingStatus.CONFIRMED)
    else:
        booking.transition_to(BookingStatus.FAILED, reason=f"payment_failed:{failure_reason}")
    return True


class PaymentService:
    def __init__(self, session: AsyncSession, gateway: PaymentGateway) -> None:
        self._session = session
        self._gateway = gateway
        self._bookings = BookingRepository(session)
        self._payments = PaymentRepository(session)

    async def pay(
        self, user: User, data: PaymentCreate, *, idempotency_key: str | None = None
    ) -> PaymentOutcome:
        # Lock the booking for the whole attempt: concurrent requests to pay the same
        # booking are serialised, so only one can see it PENDING.
        booking = await self._bookings.get(data.booking_id, for_update=True)
        if booking is None or booking.user_id != user.id:
            raise NotFoundError("Booking not found", code="BOOKING_NOT_FOUND")

        if idempotency_key is not None:
            previous = await self._payments.get_by_idempotency_key(booking.id, idempotency_key)
            if previous is not None:
                logger.info("payment.idempotent_replay", payment_id=str(previous.id))
                return PaymentOutcome(PaymentRead.model_validate(previous), replayed=True)

        if booking.status is not BookingStatus.PENDING:
            raise ConflictError(
                "Only PENDING bookings can be paid",
                code="BOOKING_NOT_PAYABLE",
                details={"booking_status": booking.status.value},
            )
        if booking.appointment_at <= datetime.now(UTC):
            raise ConflictError("The appointment time has passed", code="APPOINTMENT_PASSED")

        payment = Payment(
            booking=booking,
            amount=booking.amount,  # never a client-supplied amount
            currency=booking.currency,
            status=PaymentStatus.PENDING,
            provider=self._gateway.name,
            provider_reference=self._gateway.new_reference(),
            idempotency_key=idempotency_key,
        )
        self._payments.add(payment)

        # NOTE: the mock gateway answers instantly. With a real provider, the network call
        # should not run while holding a row lock; the PENDING payment would be committed
        # first and the result applied afterwards (typically via the webhook).
        result = await self._gateway.charge(
            ChargeRequest(
                reference=payment.provider_reference,
                amount=payment.amount,
                currency=payment.currency,
                payment_method=data.payment_method,
            )
        )
        apply_payment_result(booking, payment, result.status, result.failure_reason)
        await self._session.commit()

        logger.info(
            "payment.charged",
            payment_id=str(payment.id),
            booking_id=str(booking.id),
            status=payment.status.value,
            amount=str(payment.amount),
        )
        return PaymentOutcome(PaymentRead.model_validate(payment), replayed=False)

    async def list_payments(self, user: User, params: PageParams) -> Page[PaymentRead]:
        stmt = self._payments.list_query(user_id=None if user.is_admin else user.id)
        payments, total = await paginate(self._session, stmt, params)
        return Page[PaymentRead].build(
            [PaymentRead.model_validate(p) for p in payments], total, params
        )

    async def get(self, user: User, payment_id: UUID) -> PaymentRead:
        payment = await self._payments.get(payment_id)
        if payment is None or (payment.booking.user_id != user.id and not user.is_admin):
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        return PaymentRead.model_validate(payment)
