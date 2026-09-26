"""HMAC signatures for provider webhooks (Stripe-style).

Header format:  X-Eve-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>[,v1=<...>]

The signed message is f"{t}.{raw_body}". Binding the timestamp into the signature and
rejecting old timestamps stops a captured request from being replayed later; several `v1`
entries allow the shared secret to be rotated without downtime.
"""

import hashlib
import hmac
import time

from eve.core.errors import AuthenticationError

SIGNATURE_HEADER = "X-Eve-Signature"
SCHEME = "v1"


class InvalidSignatureError(AuthenticationError):
    code = "INVALID_SIGNATURE"


def _digest(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign(body: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Build the signature header value for `body` (used by the mock provider and tests)."""
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},{SCHEME}={_digest(secret, ts, body)}"


def verify(
    header: str | None,
    body: bytes,
    secret: str,
    *,
    tolerance_seconds: int,
    now: int | None = None,
) -> None:
    """Raise InvalidSignatureError unless `header` is a fresh, valid signature of `body`."""
    if not header:
        raise InvalidSignatureError("Missing webhook signature")

    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == SCHEME and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        raise InvalidSignatureError("Malformed webhook signature")

    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise InvalidSignatureError("Webhook signature timestamp is outside the tolerance")

    expected = _digest(secret, timestamp, body)
    # Constant-time comparison: no timing side channel on how many characters matched.
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise InvalidSignatureError("Webhook signature does not match")
