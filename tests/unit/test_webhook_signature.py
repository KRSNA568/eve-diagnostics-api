import pytest

from eve.payments.webhook_signature import InvalidSignatureError, sign, verify

SECRET = "unit-test-webhook-secret-at-least-32-bytes"
BODY = b'{"event_id":"evt_1","type":"payment.succeeded"}'
NOW = 1_800_000_000


def test_valid_signature_is_accepted() -> None:
    verify(sign(BODY, SECRET, timestamp=NOW), BODY, SECRET, tolerance_seconds=300, now=NOW)


def test_signature_header_format() -> None:
    header = sign(BODY, SECRET, timestamp=NOW)

    t_part, v1_part = header.split(",")
    assert t_part == f"t={NOW}"
    assert v1_part.startswith("v1=")
    assert len(v1_part.removeprefix("v1=")) == 64  # hex SHA-256


@pytest.mark.parametrize(
    ("header", "body", "secret", "message"),
    [
        pytest.param(None, BODY, SECRET, "Missing", id="missing"),
        pytest.param("", BODY, SECRET, "Missing", id="empty"),
        pytest.param("v1=abc", BODY, SECRET, "Malformed", id="no-timestamp"),
        pytest.param(f"t={NOW}", BODY, SECRET, "Malformed", id="no-signature"),
        pytest.param(
            sign(BODY, SECRET, timestamp=NOW),
            BODY + b" ",
            SECRET,
            "does not match",
            id="tampered-body",
        ),
        pytest.param(
            sign(BODY, "x" * 40, timestamp=NOW), BODY, SECRET, "does not match", id="wrong-secret"
        ),
        pytest.param(
            sign(BODY, SECRET, timestamp=NOW - 301), BODY, SECRET, "tolerance", id="too-old"
        ),
        pytest.param(
            sign(BODY, SECRET, timestamp=NOW + 301), BODY, SECRET, "tolerance", id="from-the-future"
        ),
    ],
)
def test_invalid_signatures_are_rejected(
    header: str | None, body: bytes, secret: str, message: str
) -> None:
    with pytest.raises(InvalidSignatureError, match=message) as exc_info:
        verify(header, body, secret, tolerance_seconds=300, now=NOW)
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "INVALID_SIGNATURE"


def test_timestamp_cannot_be_swapped_without_breaking_the_signature() -> None:
    original = sign(BODY, SECRET, timestamp=NOW - 1000)
    replayed = original.replace(f"t={NOW - 1000}", f"t={NOW}")

    with pytest.raises(InvalidSignatureError, match="does not match"):
        verify(replayed, BODY, SECRET, tolerance_seconds=300, now=NOW)


def test_any_matching_v1_signature_is_accepted_during_secret_rotation() -> None:
    old_secret = "old-webhook-secret-that-is-at-least-32-bytes"
    signed_with_old = sign(BODY, old_secret, timestamp=NOW).split(",")[1]
    signed_with_new = sign(BODY, SECRET, timestamp=NOW).split(",")[1]
    header = f"t={NOW},{signed_with_old},{signed_with_new}"

    verify(header, BODY, SECRET, tolerance_seconds=300, now=NOW)
