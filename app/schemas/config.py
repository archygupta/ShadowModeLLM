"""Schemas for the runtime configuration endpoint."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import AppBaseModel


class ConfigUpdateRequest(AppBaseModel):
    """Runtime configuration update payload."""

    shadow_percentage: int = Field(
        ...,
        ge=0,
        le=100,
        description="Percentage of requests mirrored to the candidate (0-100).",
    )


class ConfigResponse(AppBaseModel):
    """Current runtime configuration."""

    shadow_percentage: int = Field(
        ..., description="Active percentage of requests mirrored to the candidate."
    )
