"""Bounded-concurrency executor for detached shadow work.

Why this exists (memory-growth prevention)
-------------------------------------------
The naive approach - ``asyncio.create_task()`` per request - places no bound on
how many shadow jobs can exist at once. Under heavy or bursty traffic, or if the
candidate endpoint is slow, tasks accumulate faster than they complete. Each
in-flight job retains a coroutine frame, the captured request payload, a
primary-response future, and (often) an open connection. With no ceiling, this
grows unbounded and the process eventually exhausts memory / file descriptors -
and, importantly, that pressure can degrade the *primary* request path too.

A fixed worker pool draining a bounded :class:`asyncio.Queue` caps the total
outstanding shadow work at ``queue_size + workers`` regardless of request rate.
When the queue is full we drop the job immediately (O(1), non-blocking) instead
of buffering it, so memory stays flat under load and the primary path is never
slowed or starved. Shadow evaluation is best-effort, so dropping excess is the
correct trade-off.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)

# A shadow job is a zero-arg factory producing the coroutine to run. Using a
# factory (rather than a bare coroutine) means a dropped job never creates a
# coroutine, avoiding "coroutine was never awaited" warnings.
ShadowJob = Callable[[], Awaitable[None]]


class ShadowExecutor:
    """Fixed-size worker pool consuming a bounded queue of shadow jobs."""

    def __init__(
        self, *, queue_size: int, workers: int, metrics_service: MetricsService
    ) -> None:
        # maxsize must be >= 1; a value of 0 would make asyncio.Queue unbounded.
        self._queue: asyncio.Queue[ShadowJob] = asyncio.Queue(maxsize=max(1, queue_size))
        self._num_workers = max(1, workers)
        self._metrics = metrics_service
        self._workers: list[asyncio.Task] = []
        self._started = False

    async def start(self) -> None:
        """Launch the fixed worker pool (idempotent)."""

        if self._started:
            return
        self._started = True
        for i in range(self._num_workers):
            self._workers.append(
                asyncio.create_task(self._worker(i), name=f"shadow-worker-{i}")
            )
        logger.info(
            "shadow_executor.started",
            extra={"workers": self._num_workers, "queue_size": self._queue.maxsize},
        )

    async def submit(self, job: ShadowJob, *, request_id: str) -> bool:
        """Enqueue a shadow job without blocking.

        Returns ``True`` if accepted, ``False`` if the queue was full and the job
        was dropped. Dropping records the ``shadow_dropped`` metric and logs. This
        is O(1) and never blocks, so the primary path is unaffected.
        """

        try:
            self._queue.put_nowait(job)
            return True
        except asyncio.QueueFull:
            await self._metrics.record_shadow_dropped()
            logger.warning(
                "shadow.dropped",
                extra={
                    "request_id": request_id,
                    "reason": "queue_full",
                    "queue_size": self._queue.maxsize,
                },
            )
            return False

    async def stop(self) -> None:
        """Cancel and drain all workers (used on shutdown)."""

        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._started = False

    @property
    def pending(self) -> int:
        """Number of jobs currently queued (not yet picked up)."""

        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """Whether the worker pool has been started (used by readiness)."""

        return self._started

    async def _worker(self, index: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a worker must never die on a job
                logger.exception(
                    "shadow_executor.job_error", extra={"worker": index}
                )
            finally:
                self._queue.task_done()
