import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.auth.models import User
from eve.auth.repository import UserRepository
from eve.auth.schemas import (
    AccessTokenResponse,
    LoginRequest,
    SignupRequest,
    TokenPairResponse,
)
from eve.core.config import Settings
from eve.core.db import violated_constraint
from eve.core.errors import AuthenticationError, ConflictError
from eve.core.security import (
    TokenType,
    burn_password_check,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

logger = structlog.get_logger(__name__)

EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)

    async def signup(self, data: SignupRequest) -> tuple[User, TokenPairResponse]:
        user = User(
            email=data.email,
            password_hash=hash_password(data.password.get_secret_value()),
            full_name=data.full_name,
            phone=data.phone,
        )
        self._users.add(user)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            # Rely on the unique constraint rather than a prior lookup: two concurrent
            # signups with the same email cannot both succeed.
            if violated_constraint(exc) == EMAIL_UNIQUE_CONSTRAINT:
                raise ConflictError(
                    "An account with this email already exists", code="EMAIL_TAKEN"
                ) from exc
            raise

        logger.info("user.signed_up", user_id=str(user.id))
        return user, self._issue_token_pair(user)

    async def login(self, data: LoginRequest) -> TokenPairResponse:
        password = data.password.get_secret_value()
        user = await self._users.get_by_email(data.email)
        if user is None:
            burn_password_check(password)
            raise self._invalid_credentials()

        valid, upgraded_hash = verify_password(password, user.password_hash)
        if not valid:
            raise self._invalid_credentials()
        if not user.is_active:
            raise AuthenticationError("This account is disabled", code="ACCOUNT_DISABLED")

        if upgraded_hash is not None:
            user.password_hash = upgraded_hash
            await self._session.commit()

        logger.info("user.logged_in", user_id=str(user.id))
        return self._issue_token_pair(user)

    async def refresh(self, refresh_token: str) -> AccessTokenResponse:
        claims = decode_token(refresh_token, TokenType.REFRESH, self._settings)
        user = await self._users.get(claims.subject)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid token", code="INVALID_TOKEN")

        access = create_token(user.id, TokenType.ACCESS, self._settings)
        return AccessTokenResponse(access_token=access.token, expires_in=access.expires_in)

    def _issue_token_pair(self, user: User) -> TokenPairResponse:
        access = create_token(user.id, TokenType.ACCESS, self._settings)
        refresh = create_token(user.id, TokenType.REFRESH, self._settings)
        return TokenPairResponse(
            access_token=access.token,
            expires_in=access.expires_in,
            refresh_token=refresh.token,
            refresh_expires_in=refresh.expires_in,
        )

    @staticmethod
    def _invalid_credentials() -> AuthenticationError:
        # One message for "no such user" and "wrong password": no account enumeration.
        return AuthenticationError("Invalid email or password", code="INVALID_CREDENTIALS")
