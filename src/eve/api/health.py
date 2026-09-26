import asyncio
from collections.abc import Awaitable
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from eve.api.deps import SessionDep

router = APIRouter(prefix="/health", tags=["health"])

PROBE_TIMEOUT_SECONDS = 2.0

CheckResult = Literal["ok", "unavailable"]


class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessStatus(BaseModel):
    status: CheckResult
    checks: dict[str, CheckResult]


async def _probe(check: Awaitable[Any]) -> CheckResult:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            await check
    except Exception:
        return "unavailable"
    return "ok"


@router.get("/live/", summary="Liveness probe")
async def live() -> HealthStatus:
    """The process is up and serving requests. Does not touch dependencies."""
    return HealthStatus()


@router.get(
    "/ready/",
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessStatus}},
)
async def ready(session: SessionDep, response: Response) -> ReadinessStatus:
    """The service can handle traffic: every backing dependency answers."""
    checks = {"database": await _probe(session.execute(text("SELECT 1")))}

    healthy = all(result == "ok" for result in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessStatus(status="ok" if healthy else "unavailable", checks=checks)
