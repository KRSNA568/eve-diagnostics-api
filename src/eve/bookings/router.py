from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from eve.api.deps import SessionDep, SettingsDep
from eve.auth.dependencies import CurrentUser
from eve.bookings.schemas import BookingCreate, BookingRead
from eve.bookings.service import BookingService
from eve.bookings.state import BookingStatus
from eve.core.errors import ErrorResponse
from eve.core.pagination import Page, PageParamsDep


def get_booking_service(session: SessionDep, settings: SettingsDep) -> BookingService:
    return BookingService(session, settings)


BookingServiceDep = Annotated[BookingService, Depends(get_booking_service)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Book a diagnostic test (status PENDING until paid)",
    responses=_ERRORS | {status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def create_booking(
    payload: BookingCreate, user: CurrentUser, service: BookingServiceDep
) -> BookingRead:
    return await service.create(user, payload)


@router.get("/", summary="Your bookings, newest first (admins see all)", responses=_ERRORS)
async def list_bookings(
    user: CurrentUser,
    service: BookingServiceDep,
    params: PageParamsDep,
    status_filter: Annotated[BookingStatus | None, Query(alias="status")] = None,
) -> Page[BookingRead]:
    return await service.list_bookings(user, params, status=status_filter)


@router.get("/{booking_id}/", summary="Get one of your bookings", responses=_ERRORS)
async def get_booking(
    booking_id: UUID, user: CurrentUser, service: BookingServiceDep
) -> BookingRead:
    return await service.get(user, booking_id)


@router.post(
    "/{booking_id}/cancel/",
    summary="Cancel a booking (idempotent)",
    responses=_ERRORS | {status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def cancel_booking(
    booking_id: UUID, user: CurrentUser, service: BookingServiceDep
) -> BookingRead:
    return await service.cancel(user, booking_id)
