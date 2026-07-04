"""Unit tests for :class:`ShadowExecutor` (bounded concurrency + drops)."""

from __future__ import annotations

import asyncio

from app.services.metrics_service import MetricsService
from app.utils.shadow_executor import ShadowExecutor


async def test_submitted_job_runs() -> None:
    metrics = MetricsService()
    executor = ShadowExecutor(queue_size=8, workers=2, metrics_service=metrics)
    await executor.start()

    ran = asyncio.Event()

    async def job() -> None:
        ran.set()

    accepted = await executor.submit(lambda: job(), request_id="r-1")
    assert accepted is True
    await asyncio.wait_for(ran.wait(), timeout=1.0)

    await executor.stop()


async def test_queue_full_drops_and_records_metric() -> None:
    metrics = MetricsService()
    # queue_size=1, no workers started => the queue cannot drain.
    executor = ShadowExecutor(queue_size=1, workers=1, metrics_service=metrics)

    async def noop() -> None:  # pragma: no cover - never actually awaited
        return None

    first = await executor.submit(lambda: noop(), request_id="r-1")
    second = await executor.submit(lambda: noop(), request_id="r-2")
    third = await executor.submit(lambda: noop(), request_id="r-3")

    assert first is True  # fills the single slot
    assert second is False  # dropped
    assert third is False  # dropped
    assert executor.pending == 1

    snap = await metrics.snapshot()
    assert snap["shadow_dropped"] == 2


async def test_worker_survives_job_exception() -> None:
    metrics = MetricsService()
    executor = ShadowExecutor(queue_size=8, workers=1, metrics_service=metrics)
    await executor.start()

    async def boom() -> None:
        raise RuntimeError("job failed")

    second_ran = asyncio.Event()

    async def ok() -> None:
        second_ran.set()

    await executor.submit(lambda: boom(), request_id="r-1")
    await executor.submit(lambda: ok(), request_id="r-2")

    # The pool must keep processing after a failing job.
    await asyncio.wait_for(second_ran.wait(), timeout=1.0)
    await executor.stop()


async def test_start_is_idempotent_and_stop_clears_workers() -> None:
    metrics = MetricsService()
    executor = ShadowExecutor(queue_size=4, workers=2, metrics_service=metrics)
    await executor.start()
    await executor.start()  # no-op second call
    assert len(executor._workers) == 2  # noqa: SLF001 - white-box check

    await executor.stop()
    assert executor._workers == []  # noqa: SLF001
