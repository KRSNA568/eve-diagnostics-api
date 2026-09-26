"""Background jobs for payments, run by the ARQ worker."""

import random
from typing import Any
from uuid import UUID

import structlog
from arq import Retry

from eve.core.config import Settings
from eve.payments.webhook import WebhookService

logger = structlog.get_logger(__name__)

PROCESS_WEBHOOK_JOB = "process_webhook_event"

_jitter_rng = random.Random()  # noqa: S311 - retry scheduling, not security


def webhook_job_id(event_pk: UUID) -> str:
    return f"webhook:{event_pk}"


def retry_delay(
    attempt: int, *, base: float, cap: float, rng: random.Random | None = None
) -> float:
    """Exponential backoff with jitter: base * 2^(attempt-1), capped, plus up to `base` of
    random jitter so a burst of failures does not retry in lockstep."""
    jitter = (rng or _jitter_rng).uniform(0, base)
    return min(base * 2.0 ** (attempt - 1), cap) + jitter


async def process_webhook_event(ctx: dict[str, Any], event_pk: str) -> str | None:
    """Apply one stored webhook event, retrying transient failures with backoff.

    `WebhookService.process` is idempotent, so a retry - or ARQ re-running the job after a
    worker crash - can never apply an event twice. Business-level rejections are handled
    inside it (event marked FAILED, no retry); anything raised here is unexpected
    (database outage, bug) and is retried until `webhook_max_attempts`, then dead-lettered.
    """
    settings: Settings = ctx["settings"]
    attempt: int = ctx["job_try"]
    event_id = UUID(event_pk)
    structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"), attempt=attempt)

    try:
        async with ctx["session_factory"]() as session:
            status = await WebhookService(session).process(event_id)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if attempt >= settings.webhook_max_attempts:
            async with ctx["session_factory"]() as session:
                await WebhookService(session).dead_letter(event_id, error=error, attempts=attempt)
            logger.error("webhook.dead_lettered", event_pk=event_pk, error=error)
            return "FAILED"
        delay = retry_delay(
            attempt,
            base=settings.webhook_retry_base_seconds,
            cap=settings.webhook_retry_max_seconds,
        )
        logger.warning(
            "webhook.retry_scheduled", event_pk=event_pk, delay=round(delay, 2), error=error
        )
        raise Retry(defer=delay) from exc

    return status.value if status else None
