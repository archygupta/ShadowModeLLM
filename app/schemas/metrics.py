"""Response schema for the ``GET /metrics`` endpoint."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import AppBaseModel


class MetricsResponse(AppBaseModel):
    """Point-in-time snapshot of in-memory operational metrics."""

    total_requests: int = Field(..., description="Chat requests received.")
    primary_success: int = Field(..., description="Primary calls that returned a response.")
    primary_failures: int = Field(..., description="Primary calls that failed (no response).")
    candidate_success: int = Field(..., description="Candidate calls that returned a response.")
    candidate_failures: int = Field(..., description="Candidate calls that failed (non-timeout).")
    candidate_timeouts: int = Field(..., description="Candidate calls that timed out.")
    shadow_dropped: int = Field(..., description="Shadow calls skipped (disabled/unconfigured).")
    evaluation_runs: int = Field(..., description="Evaluations performed.")
    exact_matches: int = Field(..., description="Evaluations where actions matched exactly.")
    json_parse_failures: int = Field(
        ..., description="Evaluations where a response was not valid JSON."
    )
    average_primary_latency_ms: float = Field(
        ..., description="Mean primary latency over successful calls."
    )
    average_candidate_latency_ms: float = Field(
        ..., description="Mean candidate latency over successful calls."
    )
    match_rate_percent: float = Field(
        ..., description="exact_matches / evaluation_runs * 100 (0 when no runs)."
    )
