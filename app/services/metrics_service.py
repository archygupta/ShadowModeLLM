"""In-memory metrics collection for shadow-mode operation.

A single :class:`MetricsService` instance is shared for the process lifetime
(created in the app lifespan). All mutations are guarded by an
:class:`asyncio.Lock` so concurrent requests update counters safely.

Averages are computed from running sums/counts, and derived fields (averages,
match rate) are calculated at snapshot time. Everything is in-memory and resets
on restart; persistence is out of scope.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Names of the raw integer counters exposed in every snapshot.
_COUNTER_NAMES = (
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
)


class MetricsService:
    """Thread-safe (async) in-memory counters and latency accumulators."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._counters: dict[str, int] = {name: 0 for name in _COUNTER_NAMES}
        self._primary_latency_sum_ms: float = 0.0
        self._primary_latency_count: int = 0
        self._candidate_latency_sum_ms: float = 0.0
        self._candidate_latency_count: int = 0

    async def record_request(self) -> None:
        async with self._lock:
            self._counters["total_requests"] += 1

    async def record_primary_success(self, latency_ms: float) -> None:
        async with self._lock:
            self._counters["primary_success"] += 1
            self._primary_latency_sum_ms += latency_ms
            self._primary_latency_count += 1

    async def record_primary_failure(self) -> None:
        async with self._lock:
            self._counters["primary_failures"] += 1

    async def record_candidate_success(self, latency_ms: float) -> None:
        async with self._lock:
            self._counters["candidate_success"] += 1
            self._candidate_latency_sum_ms += latency_ms
            self._candidate_latency_count += 1

    async def record_candidate_failure(self) -> None:
        async with self._lock:
            self._counters["candidate_failures"] += 1

    async def record_candidate_timeout(self) -> None:
        async with self._lock:
            self._counters["candidate_timeouts"] += 1

    async def record_shadow_dropped(self) -> None:
        async with self._lock:
            self._counters["shadow_dropped"] += 1

    async def record_evaluation(self, *, exact_match: bool, json_valid: bool) -> None:
        async with self._lock:
            self._counters["evaluation_runs"] += 1
            if exact_match:
                self._counters["exact_matches"] += 1
            if not json_valid:
                self._counters["json_parse_failures"] += 1

    async def snapshot(self) -> dict[str, Any]:
        """Return a consistent point-in-time view including derived fields."""

        async with self._lock:
            counters = dict(self._counters)
            avg_primary = (
                self._primary_latency_sum_ms / self._primary_latency_count
                if self._primary_latency_count
                else 0.0
            )
            avg_candidate = (
                self._candidate_latency_sum_ms / self._candidate_latency_count
                if self._candidate_latency_count
                else 0.0
            )
            evaluation_runs = counters["evaluation_runs"]
            match_rate = (
                counters["exact_matches"] / evaluation_runs * 100.0
                if evaluation_runs
                else 0.0
            )

        return {
            **counters,
            "average_primary_latency_ms": round(avg_primary, 2),
            "average_candidate_latency_ms": round(avg_candidate, 2),
            "match_rate_percent": round(match_rate, 2),
        }
