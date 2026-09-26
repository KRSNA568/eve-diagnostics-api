import json
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response, status

from eve.api.deps import SessionDep, SettingsDep
from eve.auth.dependencies import CurrentUser
from eve.core.errors import ErrorResponse
from eve.core.pagination import Page, PageParamsDep
from eve.payments.gateway import MockPaymentGateway, PaymentGateway
from eve.payments.schemas import PaymentCreate, PaymentRead, WebhookAck, WebhookEventIn
from eve.payments.service import PaymentService
from eve.payments.webhook import WebhookService
from eve.payments.webhook_signature import SIGNATURE_HEADER, verify

logger = structlog.get_logger(__name__)


def get_payment_gateway(settings: SettingsDep) -> PaymentGateway:
    return MockPaymentGateway(settings.mock_payment_success_rate)


def get_payment_service(
    session: SessionDep, gateway: Annotated[PaymentGateway, Depends(get_payment_gateway)]
) -> PaymentService:
    return PaymentService(session, gateway)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]


def get_webhook_service(session: SessionDep) -> WebhookService:
    return WebhookService(session)


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]


async def verify_webhook_signature(
    request: Request,
    settings: SettingsDep,
    signature: Annotated[
        str | None,
        Header(alias=SIGNATURE_HEADER, description="t=<unix seconds>,v1=<hex HMAC-SHA256>"),
    ] = None,
) -> None:
    """Authenticate the provider. Runs before the body is validated, over the raw bytes."""
    verify(
        signature,
        await request.body(),
        settings.webhook_secret.get_secret_value(),
        tolerance_seconds=settings.webhook_tolerance_seconds,
    )


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


@router.post(
    "/webhook/",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_webhook_signature)],
    summary="Receive payment status events from the provider",
    description=(
        "Authenticated by an HMAC signature, not a user token. Idempotent: each `event_id` "
        "is stored and applied once; redeliveries are acknowledged with 200 `duplicate`."
    ),
    responses={
        status.HTTP_200_OK: {"model": WebhookAck, "description": "Duplicate delivery"},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    },
)
async def receive_webhook(
    event: WebhookEventIn, request: Request, response: Response, service: WebhookServiceDep
) -> WebhookAck:
    ack, event_pk = await service.receive(event, json.loads(await request.body()))
    if event_pk is None:
        response.status_code = status.HTTP_200_OK
        return ack
    try:
        await service.process(event_pk)
    except Exception:
        # The event is stored durably; failing the request would only make the provider
        # resend something we already have. Processing is retried from the inbox.
        logger.exception("webhook.processing_failed", event_id=event.event_id)
    return ack


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
