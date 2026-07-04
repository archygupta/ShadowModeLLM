"""Shared schema primitives reused across API responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AppBaseModel(BaseModel):
    """Base model applying consistent serialization behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorResponse(AppBaseModel):
    """Standard error envelope returned for non-2xx responses."""

    detail: str = Field(..., description="Human-readable error description.")
    request_id: str | None = Field(
        default=None,
        description="Correlation id for tracing the failed request in logs.",
    )
