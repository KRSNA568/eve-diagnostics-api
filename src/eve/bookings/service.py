from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.bookings.repository import BookingRepository
from eve.bookings.schemas import BookingCreate, BookingRead
from eve.bookings.state import BookingStatus
from eve.catalog.repository import CatalogRepository
from eve.core.config import Settings
from eve.core.db import violated_constraint
from eve.core.errors import ConflictError, NotFoundError, UnprocessableError
from eve.core.pagination import Page, PageParams, paginate

logger = structlog.get_logger(__name__)

ACTIVE_SLOT_UNIQUE = "uq_bookings_active_slot"


class BookingService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._bookings = BookingRepository(session)
        self._catalog = CatalogRepository(session)

    async def create(self, user: User, data: BookingCreate) -> BookingRead:
        offering = await self._catalog.get_bookable_offering(data.centre_id, data.test_id)
        if offering is None:
            raise UnprocessableError(
                "This test is not available at this centre",
                code="OFFERING_UNAVAILABLE",
                details={"centre_id": str(data.centre_id), "test_id": str(data.test_id)},
            )
        self._validate_appointment_time(data.appointment_at)

        booking = Booking(
            user_id=user.id,
            centre_id=data.centre_id,
            test_id=data.test_id,
            appointment_at=data.appointment_at,
            amount=offering.price,
            currency=offering.currency,
            status=BookingStatus.PENDING,
        )
        self._bookings.add(booking)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if violated_constraint(exc) == ACTIVE_SLOT_UNIQUE:
                raise ConflictError(
                    "You already have an active booking for this test at this time",
                    code="DUPLICATE_BOOKING",
                ) from exc
            raise

        logger.info(
            "booking.created",
            booking_id=str(booking.id),
            centre_id=str(booking.centre_id),
            test_id=str(booking.test_id),
            amount=str(booking.amount),
        )
        return await self.get(user, booking.id)

    async def list_bookings(
        self, user: User, params: PageParams, *, status: BookingStatus | None = None
    ) -> Page[BookingRead]:
        stmt = self._bookings.list_query(user_id=None if user.is_admin else user.id, status=status)
        bookings, total = await paginate(self._session, stmt, params)
        return Page[BookingRead].build(
            [BookingRead.model_validate(b) for b in bookings], total, params
        )

    async def get(self, user: User, booking_id: UUID) -> BookingRead:
        booking = await self._visible_booking(user, booking_id)
        return BookingRead.model_validate(booking)

    async def cancel(self, user: User, booking_id: UUID) -> BookingRead:
        # Row lock: a cancellation and a payment for the same booking are serialised, so
        # they can never both "win".
        booking = await self._visible_booking(user, booking_id, for_update=True)

        if booking.status is not BookingStatus.CANCELLED and booking.appointment_at <= _now():
            raise ConflictError(
                "Bookings cannot be cancelled after the appointment time",
                code="APPOINTMENT_PASSED",
            )
        changed = booking.transition_to(BookingStatus.CANCELLED, reason="cancelled_by_user")
        await self._session.commit()

        if changed:
            logger.info("booking.cancelled", booking_id=str(booking.id))
        return BookingRead.model_validate(booking)

    async def _visible_booking(
        self, user: User, booking_id: UUID, *, for_update: bool = False
    ) -> Booking:
        booking = await self._bookings.get(booking_id, for_update=for_update)
        # Someone else's booking is reported as missing, not forbidden: the response must
        # not reveal that the ID exists.
        if booking is None or (booking.user_id != user.id and not user.is_admin):
            raise NotFoundError("Booking not found", code="BOOKING_NOT_FOUND")
        return booking

    def _validate_appointment_time(self, appointment_at: datetime) -> None:
        now = _now()
        earliest = now + timedelta(minutes=self._settings.booking_min_lead_minutes)
        latest = now + timedelta(days=self._settings.booking_max_days_ahead)
        if not earliest <= appointment_at <= latest:
            raise UnprocessableError(
                "Appointment time is outside the bookable window",
                code="INVALID_APPOINTMENT_TIME",
                details={"earliest": earliest.isoformat(), "latest": latest.isoformat()},
            )


def _now() -> datetime:
    return datetime.now(UTC)
