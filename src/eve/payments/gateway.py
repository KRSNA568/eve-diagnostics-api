"""Payment gateway port and its simulated implementation.

The service depends only on the `PaymentGateway` protocol, so a real provider (Razorpay,
Stripe, ...) can be dropped in without touching business logic.
"""

import random
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

from uuid_utils.compat import uuid7

from eve.payments.models import PaymentStatus


class MockPaymentMethod(StrEnum):
    """Test payment methods, in the spirit of Stripe's `pm_card_visa` test tokens."""

    SUCCESS = "mock_card_success"  # always approved
    DECLINED = "mock_card_declined"  # always declined
    RANDOM = "mock_card_random"  # approved with the configured probability


@dataclass(frozen=True, slots=True)
class ChargeRequest:
    reference: str
    amount: Decimal
    currency: str
    payment_method: str


@dataclass(frozen=True, slots=True)
class ChargeResult:
    status: Literal[PaymentStatus.SUCCESS, PaymentStatus.FAILED]
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    name: str

    def new_reference(self) -> str: ...

    async def charge(self, request: ChargeRequest) -> ChargeResult: ...


class MockPaymentGateway:
    name = "mockpay"

    def __init__(self, success_rate: float, rng: random.Random | None = None) -> None:
        if not 0 <= success_rate <= 1:
            raise ValueError("success_rate must be between 0 and 1")
        self._success_rate = success_rate
        # Simulation only - never used for anything security-sensitive.
        self._rng = rng or random.Random()  # noqa: S311

    def new_reference(self) -> str:
        return f"mock_pay_{uuid7().hex}"

    async def charge(self, request: ChargeRequest) -> ChargeResult:
        method = MockPaymentMethod(request.payment_method)
        approved = method is MockPaymentMethod.SUCCESS or (
            method is MockPaymentMethod.RANDOM and self._rng.random() < self._success_rate
        )
        if approved:
            return ChargeResult(PaymentStatus.SUCCESS)
        return ChargeResult(PaymentStatus.FAILED, failure_reason="card_declined")
