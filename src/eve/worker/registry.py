"""Everything the ARQ worker runs, independent of how it is configured.

Kept separate from `settings.py` (which reads configuration at import time) so tests can
run these functions in an in-process burst worker.
"""

from typing import Any

import structlog
from arq import func
from arq.worker import Function

from eve.core.config import get_settings
from eve.core.db import create_engine, create_session_factory
from eve.core.logging import configure_logging
from eve.payments.jobs import PROCESS_WEBHOOK_JOB, process_webhook_event

logger = structlog.get_logger(__name__)

# Retries and dead-lettering are decided by the job itself (see `webhook_max_attempts`),
# so ARQ's own attempt limit is set well above it and never cuts that logic short.
FUNCTIONS: list[Function] = [func(process_webhook_event, name=PROCESS_WEBHOOK_JOB, max_tries=100)]


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    engine = create_engine(settings)
    ctx.update(settings=settings, engine=engine, session_factory=create_session_factory(engine))
    logger.info("worker.startup", environment=settings.environment)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()
    logger.info("worker.shutdown")
