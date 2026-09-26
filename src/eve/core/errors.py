"""Error model shared by the whole API.

Services raise `AppError` subclasses; handlers here turn them - and framework errors -
into one response envelope:

    {"error": {"code": "...", "message": "...", "details": {...}}, "request_id": "..."}
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, ClassVar

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    code: str = Field(examples=["NOT_FOUND"])
    message: str = Field(examples=["Booking not found"])
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str | None = None


class AppError(Exception):
    """An expected, client-facing error raised from the service layer."""

    status_code: ClassVar[int] = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}
        self.headers = headers


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class UnprocessableError(AppError):
    """The request is well-formed but violates a business rule."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"

    def __init__(self, message: str = "Authentication required", **kwargs: Any) -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"}, **kwargs)


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


_HTTP_STATUS_CODES = {
    status.HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
}


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details or {}),
        request_id=request_id,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # pragma: no cover - registered for AppError only
        raise exc
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
        request_id=_request_id(request),
    )


async def _handle_request_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    # The offending input is deliberately not echoed back: it may be a password or token.
    errors = [
        {
            "location": error["loc"][0] if error["loc"] else None,
            "field": ".".join(str(part) for part in error["loc"][1:]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details={"errors": errors},
        request_id=_request_id(request),
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(
        status_code=exc.status_code,
        code=_HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR"),
        message=message,
        headers=exc.headers,
        request_id=_request_id(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
