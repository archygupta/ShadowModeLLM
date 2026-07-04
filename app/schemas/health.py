"""Response models for the health/readiness endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import AppBaseModel


class HealthResponse(AppBaseModel):
    """Liveness payload confirming the process is up."""

    status: Literal["ok"] = Field(default="ok", description="Liveness marker.")
    app: str = Field(..., description="Configured application name.")
    environment: str = Field(..., description="Active deployment environment.")
    version: str = Field(..., description="Running application version.")


class ReadinessResponse(AppBaseModel):
    """Readiness payload confirming dependencies are initialized."""

    status: Literal["ready", "not_ready"] = Field(
        ..., description="Whether the service can serve traffic."
    )
    checks: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-dependency readiness results keyed by name.",
    )
