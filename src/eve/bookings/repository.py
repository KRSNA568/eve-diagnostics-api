from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from eve.bookings.models import Booking
from eve.bookings.state import BookingStatus

# Many-to-one eager loads: one JOIN, no extra round trips.
_WITH_SUMMARIES = (joinedload(Booking.centre), joinedload(Booking.test))


class BookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def list_query(self, *, user_id: UUID | None, status: BookingStatus | None) -> Select[Booking]:
        """Newest first. `user_id=None` lists every user's bookings (admin view)."""
        stmt = select(Booking).options(*_WITH_SUMMARIES)
        if user_id is not None:
            stmt = stmt.where(Booking.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Booking.status == status)
        return stmt.order_by(Booking.created_at.desc(), Booking.id.desc())

    async def get(self, booking_id: UUID, *, for_update: bool = False) -> Booking | None:
        stmt = select(Booking).options(*_WITH_SUMMARIES).where(Booking.id == booking_id)
        if for_update:
            # Lock only the booking row, not the joined centre/test rows.
            stmt = stmt.with_for_update(of=Booking)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add(self, booking: Booking) -> None:
        self._session.add(booking)
