"""Metrics endpoint exposing in-memory operational counters."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import MetricsServiceDep
from app.schemas.metrics import MetricsResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=MetricsResponse, summary="Operational metrics")
async def metrics(metrics_service: MetricsServiceDep) -> MetricsResponse:
    """Return a real-time snapshot of in-memory metrics as plain JSON."""

    snapshot = await metrics_service.snapshot()
    return MetricsResponse(**snapshot)
