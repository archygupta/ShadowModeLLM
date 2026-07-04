"""Schema for shadow-mode evaluation results.

Currently used internally (logged after each shadow comparison). Not yet exposed
through any API endpoint.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import AppBaseModel


class EvaluationResult(AppBaseModel):
    """Outcome of comparing a primary response against a candidate response."""

    json_valid: bool = Field(
        ..., description="True only when both responses parsed as valid JSON."
    )
    primary_action: Any = Field(
        default=None, description="`action` value from the primary response, or None."
    )
    candidate_action: Any = Field(
        default=None, description="`action` value from the candidate response, or None."
    )
    exact_match: bool = Field(
        ...,
        description="True when both are valid JSON and their action values match "
        "exactly (case-sensitive).",
    )
