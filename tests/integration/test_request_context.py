"""Cross-cutting HTTP behaviour: error envelope, request IDs and access logging."""

import logging
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient
from pydantic import BaseModel, Field

from eve.core.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/_test")


class Payload(BaseModel):
    password: str = Field(min_length=12)
    quantity: int


@router.get("/not-found")
async def raise_not_found() -> None:
    raise NotFoundError("Booking not found", code="BOOKING_NOT_FOUND", details={"id": "b-1"})


@router.get("/conflict")
async def raise_conflict() -> None:
    raise ConflictError("Booking is not payable")


@router.get("/crash")
async def crash() -> None:
    raise RuntimeError("database password is hunter2")


@router.post("/validate")
async def validate(payload: Payload) -> Payload:
    return payload


@pytest.fixture
async def test_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(router)
    return client


def _log_events(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == event
    ]


async def test_app_error_is_rendered_in_the_error_envelope(test_client: AsyncClient) -> None:
    response = await test_client.get("/_test/not-found")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "BOOKING_NOT_FOUND",
            "message": "Booking not found",
            "details": {"id": "b-1"},
        },
        "request_id": response.headers["X-Request-ID"],
    }


async def test_app_error_uses_the_default_code_of_its_class(test_client: AsyncClient) -> None:
    response = await test_client.get("/_test/conflict")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_unhandled_exception_returns_generic_500_without_leaking_details(
    test_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = await test_client.get("/_test/crash")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Internal server error",
        "details": {},
    }
    assert "hunter2" not in response.text
    [logged] = _log_events(caplog, "http.unhandled_exception")
    assert logged["request_id"] == body["request_id"] == response.headers["X-Request-ID"]


async def test_validation_errors_list_fields_without_echoing_input(
    test_client: AsyncClient,
) -> None:
    response = await test_client.post(
        "/_test/validate", json={"password": "hunter2", "quantity": "many"}
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {e["field"] for e in error["details"]["errors"]} == {"password", "quantity"}
    assert "hunter2" not in response.text


async def test_unknown_route_uses_the_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_wrong_method_uses_the_error_envelope(client: AsyncClient) -> None:
    response = await client.post("/api/v1/health/live/")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_valid_incoming_request_id_is_propagated(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live/", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


@pytest.mark.parametrize("forged", ["has space", "x" * 129, "evil\tinjected"])
async def test_unsafe_incoming_request_id_is_replaced(client: AsyncClient, forged: str) -> None:
    response = await client.get("/api/v1/health/live/", headers={"X-Request-ID": forged})

    request_id = response.headers["X-Request-ID"]
    assert request_id != forged
    assert len(request_id) == 32


async def test_each_request_emits_one_structured_access_log(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = await client.get("/api/v1/health/live/")

    [access] = _log_events(caplog, "http.request")
    assert access["method"] == "GET"
    assert access["path"] == "/api/v1/health/live/"
    assert access["status_code"] == 200
    assert access["request_id"] == response.headers["X-Request-ID"]
    assert access["duration_ms"] >= 0
