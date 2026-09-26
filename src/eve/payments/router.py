from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status

from eve.api.deps import SessionDep, SettingsDep
from eve.auth.dependencies import CurrentUser
from eve.core.errors import ErrorResponse
from eve.core.pagination import Page, PageParamsDep
from eve.payments.gateway import MockPaymentGateway, PaymentGateway
from eve.payments.schemas import PaymentCreate, PaymentRead
from eve.payments.service import PaymentService


def get_payment_gateway(settings: SettingsDep) -> PaymentGateway:
    return MockPaymentGateway(settings.mock_payment_success_rate)


def get_payment_service(
    session: SessionDep, gateway: Annotated[PaymentGateway, Depends(get_payment_gateway)]
) -> PaymentService:
    return PaymentService(session, gateway)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Pay for a booking (simulated gateway)",
    response_description="The payment; `status` is SUCCESS or FAILED and the booking follows.",
    responses=_ERRORS
    | {
        status.HTTP_200_OK: {
            "model": PaymentRead,
            "description": "Replay of an earlier request with the same Idempotency-Key",
        },
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def create_payment(
    payload: PaymentCreate,
    user: CurrentUser,
    service: PaymentServiceDep,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r"^[\x21-\x7e]+$",
            description="Retry-safe key: repeating a request with it returns the original result.",
        ),
    ] = None,
) -> PaymentRead:
    outcome = await service.pay(user, payload, idempotency_key=idempotency_key)
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.payment


@router.get("/", summary="Your payments, newest first (admins see all)", responses=_ERRORS)
async def list_payments(
    user: CurrentUser, service: PaymentServiceDep, params: PageParamsDep
) -> Page[PaymentRead]:
    return await service.list_payments(user, params)


@router.get("/{payment_id}/", summary="Get one of your payments", responses=_ERRORS)
async def get_payment(
    payment_id: UUID, user: CurrentUser, service: PaymentServiceDep
) -> PaymentRead:
    return await service.get(user, payment_id)
