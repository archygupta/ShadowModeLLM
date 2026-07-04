"""Unit tests for the in-memory :class:`MetricsService`."""

from __future__ import annotations

import asyncio

from app.services.metrics_service import MetricsService


async def test_initial_snapshot_is_zeroed() -> None:
    metrics = MetricsService()
    snap = await metrics.snapshot()
    for key in (
        "total_requests",
        "primary_success",
        "primary_failures",
        "candidate_success",
        "candidate_failures",
        "candidate_timeouts",
        "shadow_dropped",
        "evaluation_runs",
        "exact_matches",
        "json_parse_failures",
    ):
        assert snap[key] == 0
    assert snap["average_primary_latency_ms"] == 0.0
    assert snap["average_candidate_latency_ms"] == 0.0
    assert snap["match_rate_percent"] == 0.0


async def test_counters_increment() -> None:
    metrics = MetricsService()
    await metrics.record_request()
    await metrics.record_primary_failure()
    await metrics.record_candidate_failure()
    await metrics.record_candidate_timeout()
    await metrics.record_shadow_dropped()

    snap = await metrics.snapshot()
    assert snap["total_requests"] == 1
    assert snap["primary_failures"] == 1
    assert snap["candidate_failures"] == 1
    assert snap["candidate_timeouts"] == 1
    assert snap["shadow_dropped"] == 1


async def test_latency_averages() -> None:
    metrics = MetricsService()
    await metrics.record_primary_success(100.0)
    await metrics.record_primary_success(200.0)
    await metrics.record_candidate_success(30.0)

    snap = await metrics.snapshot()
    assert snap["primary_success"] == 2
    assert snap["average_primary_latency_ms"] == 150.0
    assert snap["candidate_success"] == 1
    assert snap["average_candidate_latency_ms"] == 30.0


async def test_evaluation_and_match_rate() -> None:
    metrics = MetricsService()
    await metrics.record_evaluation(exact_match=True, json_valid=True)
    await metrics.record_evaluation(exact_match=False, json_valid=True)
    await metrics.record_evaluation(exact_match=False, json_valid=False)

    snap = await metrics.snapshot()
    assert snap["evaluation_runs"] == 3
    assert snap["exact_matches"] == 1
    assert snap["json_parse_failures"] == 1
    assert snap["match_rate_percent"] == round(1 / 3 * 100, 2)


async def test_concurrent_updates_are_thread_safe() -> None:
    metrics = MetricsService()
    await asyncio.gather(*(metrics.record_request() for _ in range(500)))
    snap = await metrics.snapshot()
    assert snap["total_requests"] == 500
