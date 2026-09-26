import re
import time
from uuid import UUID

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid_utils.compat import uuid7

from eve.core.errors import error_response

REQUEST_ID_HEADER = "X-Request-ID"
# Dependencies may put headers here (request.state.extra_response_headers) to have them
# added to whatever response is finally sent - including error responses built by
# exception handlers, which headers set on FastAPI's injected Response never reach.
EXTRA_HEADERS_STATE = "extra_response_headers"

# Accept a caller-supplied request ID only if it is short and log-safe (no spaces or
# newlines that could forge log lines); otherwise mint our own.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

logger = structlog.get_logger(__name__)


def _request_id_from(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate: str = value.decode("latin-1")
            if _VALID_REQUEST_ID.fullmatch(candidate):
                return candidate
            break
    generated: UUID = uuid7()
    return generated.hex


class RequestContextMiddleware:
    """Request ID propagation, one structured access-log line per request, and a
    last-resort 500 response in the standard error envelope.

    Written as pure ASGI rather than `BaseHTTPMiddleware` so context variables bound here
    are visible inside endpoints and streaming responses are never buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        response_started = False
        started_at = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append(REQUEST_ID_HEADER, request_id)
                for name, value in scope["state"].get(EXTRA_HEADERS_STATE, {}).items():
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("http.unhandled_exception")
            if response_started:
                raise
            response = error_response(
                status_code=500,
                code="INTERNAL_ERROR",
                message="Internal server error",
                request_id=request_id,
            )
            await response(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
