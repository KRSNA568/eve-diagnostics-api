from typing import Annotated

import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from eve.api.deps import SessionDep, SettingsDep
from eve.auth.models import User
from eve.auth.repository import UserRepository
from eve.auth.service import AuthService
from eve.core.errors import AuthenticationError, PermissionDeniedError
from eve.core.security import TokenType, decode_token

# auto_error=False so a missing header goes through our AuthenticationError (401 with the
# standard envelope and a WWW-Authenticate header) instead of FastAPI's default response.
_bearer = HTTPBearer(auto_error=False, description="Access token from `POST /auth/login/`")


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    if credentials is None:
        raise AuthenticationError()

    claims = decode_token(credentials.credentials, TokenType.ACCESS, settings)
    user = await UserRepository(session).get(claims.subject)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid token", code="INVALID_TOKEN")

    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise PermissionDeniedError("Administrator privileges required")
    return user


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
