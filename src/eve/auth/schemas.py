from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

# Upper bound protects the server: Argon2 cost grows with input length.
MAX_PASSWORD_LENGTH = 128


class _Request(BaseModel):
    # `extra="forbid"` rejects unknown fields, so a client cannot mass-assign
    # attributes such as `is_admin` at signup.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _normalise_email(email: str) -> str:
    return email.lower()


class SignupRequest(_Request):
    email: EmailStr = Field(examples=["asha@example.com"])
    password: SecretStr = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)
    full_name: str = Field(min_length=1, max_length=120, examples=["Asha Verma"])
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{7,15}$", examples=["+919812345678"])

    _lowercase_email = field_validator("email")(_normalise_email)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, password: SecretStr) -> SecretStr:
        value = password.get_secret_value()
        if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
            raise ValueError("must contain at least one letter and one digit")
        return password


class LoginRequest(_Request):
    email: EmailStr
    # No strength rules here: login must not reveal the password policy.
    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)

    _lowercase_email = field_validator("email")(_normalise_email)


class RefreshRequest(_Request):
    refresh_token: str = Field(min_length=1)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")


class TokenPairResponse(AccessTokenResponse):
    refresh_token: str
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    phone: str | None
    is_admin: bool
    created_at: datetime


class SignupResponse(BaseModel):
    user: UserRead
    tokens: TokenPairResponse
