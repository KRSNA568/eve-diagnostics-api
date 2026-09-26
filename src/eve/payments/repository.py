from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from eve.bookings.models import Booking
from eve.payments.models import Payment


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _with_booking(self) -> Select[Payment]:
        return select(Payment).join(Payment.booking).options(contains_eager(Payment.booking))

    def list_query(self, *, user_id: UUID | None) -> Select[Payment]:
        """Newest first. `user_id=None` lists every payment (admin view)."""
        stmt = self._with_booking()
        if user_id is not None:
            stmt = stmt.where(Booking.user_id == user_id)
        return stmt.order_by(Payment.created_at.desc(), Payment.id.desc())

    async def get(self, payment_id: UUID) -> Payment | None:
        stmt = self._with_booking().where(Payment.id == payment_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(self, booking_id: UUID, key: str) -> Payment | None:
        stmt = self._with_booking().where(
            Payment.booking_id == booking_id, Payment.idempotency_key == key
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add(self, payment: Payment) -> None:
        self._session.add(payment)
