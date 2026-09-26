from typing import Any
from uuid import uuid4

import pytest

from eve.bookings.jobs import mask_email, notify_booking_confirmed


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("asha.verma@example.com", "a***@example.com"),
        ("x@clinic.in", "x***@clinic.in"),
    ],
)
def test_emails_are_masked_before_logging(email: str, masked: str) -> None:
    assert mask_email(email) == masked


async def test_a_queue_outage_never_fails_the_request_that_confirmed_the_booking() -> None:
    class BrokenQueue:
        async def enqueue(self, job: str, *args: Any, job_id: str | None = None) -> bool:
            raise ConnectionError("Redis unavailable")

    await notify_booking_confirmed(BrokenQueue(), uuid4())  # logged, not raised


async def test_no_queue_means_no_notification() -> None:
    await notify_booking_confirmed(None, uuid4())
