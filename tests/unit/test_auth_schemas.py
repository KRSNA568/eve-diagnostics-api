from typing import Any

import pytest
from pydantic import ValidationError

from eve.auth.schemas import LoginRequest, SignupRequest

VALID_SIGNUP: dict[str, Any] = {
    "email": "Asha.Verma@Example.COM",
    "password": "Password123",
    "full_name": "  Asha Verma ",
}


def test_signup_normalises_email_and_strips_whitespace() -> None:
    request = SignupRequest(**VALID_SIGNUP)

    assert request.email == "asha.verma@example.com"
    assert request.full_name == "Asha Verma"


def test_password_is_never_exposed_in_repr() -> None:
    assert "Password123" not in repr(SignupRequest(**VALID_SIGNUP))


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("Pass1", id="too-short"),
        pytest.param("passwordonly", id="no-digit"),
        pytest.param("1234567890", id="no-letter"),
        pytest.param("a1" * 65, id="too-long"),
    ],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    with pytest.raises(ValidationError, match="password"):
        SignupRequest(**(VALID_SIGNUP | {"password": password}))


@pytest.mark.parametrize("phone", ["12345", "+91-98123-45678", "phone"])
def test_invalid_phone_numbers_are_rejected(phone: str) -> None:
    with pytest.raises(ValidationError, match="phone"):
        SignupRequest(**(VALID_SIGNUP | {"phone": phone}))


def test_unknown_fields_are_rejected_to_prevent_mass_assignment() -> None:
    with pytest.raises(ValidationError, match="is_admin"):
        SignupRequest(**(VALID_SIGNUP | {"is_admin": True}))


def test_login_does_not_apply_the_password_policy() -> None:
    request = LoginRequest.model_validate({"email": "A@EXAMPLE.COM", "password": "x"})

    assert request.email == "a@example.com"
