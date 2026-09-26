from typing import Any

from fastapi import APIRouter, status

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


@router.post(
    "/signup/",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def signup(payload: SignupRequest, service: AuthServiceDep) -> SignupResponse:
    user, tokens = await service.signup(payload)
    return SignupResponse(user=UserRead.model_validate(user), tokens=tokens)


@router.post("/login/", summary="Exchange credentials for tokens", responses=_UNAUTHORIZED)
async def login(payload: LoginRequest, service: AuthServiceDep) -> TokenPairResponse:
    return await service.login(payload)


@router.post("/refresh/", summary="Get a new access token", responses=_UNAUTHORIZED)
async def refresh(payload: RefreshRequest, service: AuthServiceDep) -> AccessTokenResponse:
    return await service.refresh(payload.refresh_token)


@router.get("/me/", summary="Current user profile", responses=_UNAUTHORIZED)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
