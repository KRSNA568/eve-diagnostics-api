"""Background jobs for payments, run by the ARQ worker."""

import random
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from arq import Retry
from sqlalchemy import select

from eve.core.config import Settings
from eve.core.queue import ArqTaskQueue, TaskQueue
from eve.payments.models import WebhookEvent, WebhookEventStatus
from eve.payments.webhook import WebhookService

logger = structlog.get_logger(__name__)

PROCESS_WEBHOOK_JOB = "process_webhook_event"

_jitter_rng = random.Random()  # noqa: S311 - retry scheduling, not security


def webhook_job_id(event_pk: UUID) -> str:
    return f"webhook:{event_pk}"


def _queue(ctx: dict[str, Any]) -> TaskQueue | None:
    """The worker's own queue (ARQ puts its Redis connection in ctx["redis"])."""
    return ArqTaskQueue(ctx["redis"]) if "redis" in ctx else None


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
            status = await WebhookService(session, _queue(ctx)).process(event_id)
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


async def requeue_stale_webhook_events(ctx: dict[str, Any]) -> int:
    """Re-queue events stuck in RECEIVED - e.g. Redis was down when they arrived.

    The inbox table is the source of truth and the queue only a delivery mechanism, so
    nothing received can be lost. Re-queueing uses the event's normal job id, so an event
    whose job is still waiting in the queue is not queued twice.
    """
    settings: Settings = ctx["settings"]
    queue = _queue(ctx)
    if queue is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.webhook_stale_after_seconds)

    async with ctx["session_factory"]() as session:
        stmt = select(WebhookEvent.id).where(
            WebhookEvent.status == WebhookEventStatus.RECEIVED, WebhookEvent.created_at < cutoff
        )
        stale = (await session.execute(stmt)).scalars().all()

    requeued = 0
    for event_pk in stale:
        if await queue.enqueue(PROCESS_WEBHOOK_JOB, str(event_pk), job_id=webhook_job_id(event_pk)):
            requeued += 1
    if stale:
        logger.warning("webhook.stale_events_requeued", found=len(stale), requeued=requeued)
    return requeued
