"""ARQ worker entry point:  uv run arq eve.worker.settings.WorkerSettings"""

from typing import ClassVar

from arq.connections import RedisSettings
from arq.cron import CronJob
from arq.worker import Function

from eve.core.config import get_settings
from eve.worker.registry import CRON_JOBS, FUNCTIONS, shutdown, startup


class WorkerSettings:
    functions: ClassVar[list[Function]] = FUNCTIONS
    cron_jobs: ClassVar[list[CronJob]] = CRON_JOBS
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 60
    keep_result = 3600  # seconds a finished job's id keeps blocking duplicate enqueues
