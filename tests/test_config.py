"""Unit tests for settings loading and the runtime-config seam."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Environment
from tests.conftest import make_settings


def test_env_aliases_are_applied() -> None:
    settings = make_settings(
        PRIMARY_LLM_ENDPOINT="https://x.test/v1",
        SHADOW_PERCENTAGE=42,
        ENVIRONMENT="production",
    )
    assert settings.primary_llm_endpoint == "https://x.test/v1"
    assert settings.shadow_percentage == 42
    assert settings.environment is Environment.PRODUCTION
    assert settings.is_production is True


def test_new_backoff_cap_default() -> None:
    settings = make_settings()
    assert settings.primary_llm_backoff_max_seconds == 8.0


def test_model_fields_present() -> None:
    settings = make_settings(PRIMARY_LLM_MODEL="m-primary", CANDIDATE_LLM_MODEL="m-cand")
    assert settings.primary_llm_model == "m-primary"
    assert settings.candidate_llm_model == "m-cand"


@pytest.mark.parametrize("bad", [-1, 101])
def test_shadow_percentage_bounds(bad: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(SHADOW_PERCENTAGE=bad)


def test_unknown_env_vars_are_ignored() -> None:
    # extra="ignore" => stray env keys don't break startup.
    settings = make_settings(SOME_UNKNOWN_KEY="whatever")
    assert settings.app_name  # constructed fine
