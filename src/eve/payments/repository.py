from typing import Any
from uuid import UUID

from sqlalchemy import Select, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from eve.bookings.models import Booking
from eve.payments.models import Payment, PaymentStatus, WebhookEvent, WebhookEventStatus


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

    async def get_by_reference(self, provider_reference: str) -> Payment | None:
        stmt = (
            self._with_booking()
            .where(Payment.provider_reference == provider_reference)
            .with_for_update(of=Payment)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def has_successful_payment(self, booking_id: UUID) -> bool:
        stmt = select(
            exists().where(
                Payment.booking_id == booking_id, Payment.status == PaymentStatus.SUCCESS
            )
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    def add(self, payment: Payment) -> None:
        self._session.add(payment)


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(
        self, *, event_id: str, event_type: str, payload: dict[str, Any]
    ) -> UUID | None:
        """Store the event unless its `event_id` was seen before.

        One atomic statement (INSERT ... ON CONFLICT DO NOTHING), so two concurrent
        deliveries of the same event cannot both be stored. Returns the new row's id, or None
        for a duplicate.
        """
        stmt = (
            insert(WebhookEvent)
            .values(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                status=WebhookEventStatus.RECEIVED,
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
            .returning(WebhookEvent.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def list_query(self, *, status: WebhookEventStatus | None) -> Select[WebhookEvent]:
        stmt = select(WebhookEvent)
        if status is not None:
            stmt = stmt.where(WebhookEvent.status == status)
        return stmt.order_by(WebhookEvent.created_at.desc(), WebhookEvent.id.desc())

    async def get_for_update(self, event_pk: UUID) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(WebhookEvent.id == event_pk).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()
