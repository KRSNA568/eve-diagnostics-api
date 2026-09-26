from typing import Any

from fastapi import APIRouter, Depends, status

from eve.api.rate_limit import limit_by_ip
from eve.auth.dependencies import AuthServiceDep, CurrentUser
from eve.auth.schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenPairResponse,
    UserRead,
)
from eve.core.errors import ErrorResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}
}
_CONFLICT: dict[int | str, dict[str, Any]] = {status.HTTP_409_CONFLICT: {"model": ErrorResponse}}
_RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse}
}


@router.post(
    "/signup/",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    dependencies=[Depends(limit_by_ip("rate_limit_signup", scope="auth.signup"))],
    responses=_CONFLICT | _RATE_LIMITED,
)
async def signup(payload: SignupRequest, service: AuthServiceDep) -> SignupResponse:
    user, tokens = await service.signup(payload)
    return SignupResponse(user=UserRead.model_validate(user), tokens=tokens)


@router.post(
    "/login/",
    summary="Exchange credentials for tokens",
    dependencies=[Depends(limit_by_ip("rate_limit_login", scope="auth.login"))],
    responses=_UNAUTHORIZED | _RATE_LIMITED,
)
async def login(payload: LoginRequest, service: AuthServiceDep) -> TokenPairResponse:
    return await service.login(payload)


@router.post("/refresh/", summary="Get a new access token", responses=_UNAUTHORIZED)
async def refresh(payload: RefreshRequest, service: AuthServiceDep) -> AccessTokenResponse:
    return await service.refresh(payload.refresh_token)


@router.get("/me/", summary="Current user profile", responses=_UNAUTHORIZED)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
