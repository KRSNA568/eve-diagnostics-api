from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/live/", summary="Liveness probe")
async def live() -> HealthStatus:
    """The process is up and serving requests. Does not touch dependencies."""
    return HealthStatus()
