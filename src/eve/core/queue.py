"""Background job queue port.

Feature code asks for work to be done later through `TaskQueue`; `ArqTaskQueue` is the
Redis/ARQ adapter. Keeping the port tiny keeps the queue technology replaceable and lets
tests substitute an in-process queue.
"""

from typing import Any, Protocol

from arq.connections import ArqRedis


class TaskQueue(Protocol):
    async def enqueue(self, job: str, *args: Any, job_id: str | None = None) -> bool:
        """Queue `job(*args)`. Returns False if a job with `job_id` is already queued."""
        ...


class ArqTaskQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(self, job: str, *args: Any, job_id: str | None = None) -> bool:
        # With `_job_id`, ARQ refuses to queue a second job with the same ID while the first
        # is pending or its result is kept - a queue-level deduplication layer.
        return await self._redis.enqueue_job(job, *args, _job_id=job_id) is not None
