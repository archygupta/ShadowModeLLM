"""Application configuration loaded from environment variables and ``.env``.

Settings are validated with pydantic-settings (pydantic v2). Access them via
``get_settings()`` which is cached so the ``.env`` file is parsed only once per
process.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Deployment environment the service is running in."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from the environment.

    Values are read (in order of precedence) from real environment variables
    and then from the ``.env`` file. Unknown keys are ignored so operators can
    keep extra variables in their environment without breaking startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application metadata ---------------------------------------------
    app_name: str = Field(
        default="shadow-mode-llm-evaluator",
        validation_alias="APP_NAME",
        description="Human-readable service name used in logs and responses.",
    )
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias="ENVIRONMENT",
        description="Deployment environment.",
    )

    # --- HTTP server ------------------------------------------------------
    host: str = Field(
        default="0.0.0.0",
        validation_alias="HOST",
        description="Interface uvicorn binds to.",
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias="PORT",
        description="Port uvicorn binds to.",
    )

    # --- Logging ----------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
        description="Root log level.",
    )
    log_json: bool = Field(
        default=True,
        validation_alias="LOG_JSON",
        description="Emit structured JSON logs when true, human-readable otherwise.",
    )

    # --- Outbound HTTP client ---------------------------------------------
    http_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="HTTP_TIMEOUT_SECONDS",
        description="Default timeout applied to the shared httpx.AsyncClient.",
    )

    # --- Primary LLM (DigitalOcean Serverless Inference) ------------------
    primary_llm_endpoint: str = Field(
        default="https://inference.do-ai.run/v1/chat/completions",
        validation_alias="PRIMARY_LLM_ENDPOINT",
        description="Full URL of the primary LLM chat completions endpoint.",
    )
    primary_llm_model: str = Field(
        default="llama3.3-70b-instruct",
        validation_alias="PRIMARY_LLM_MODEL",
        description="Default model applied to the payload when it omits 'model'.",
    )
    primary_llm_api_key: str = Field(
        default="",
        validation_alias="PRIMARY_LLM_API_KEY",
        description="Bearer token (DigitalOcean model access key) for the primary LLM.",
    )
    primary_llm_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="PRIMARY_LLM_TIMEOUT_SECONDS",
        description="Per-request timeout for calls to the primary LLM.",
    )
    primary_llm_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        validation_alias="PRIMARY_LLM_MAX_RETRIES",
        description="Max retries (beyond the first attempt) for transient failures.",
    )
    primary_llm_backoff_base_seconds: float = Field(
        default=0.5,
        gt=0,
        validation_alias="PRIMARY_LLM_BACKOFF_BASE_SECONDS",
        description="Base delay for exponential backoff between retries.",
    )
    primary_llm_backoff_max_seconds: float = Field(
        default=8.0,
        gt=0,
        validation_alias="PRIMARY_LLM_BACKOFF_MAX_SECONDS",
        description="Upper bound on any single backoff/Retry-After sleep.",
    )

    # --- Candidate LLM (shadow mode) --------------------------------------
    candidate_llm_enabled: bool = Field(
        default=True,
        validation_alias="CANDIDATE_LLM_ENABLED",
        description="Toggle shadow-mode calls to the candidate LLM.",
    )
    candidate_llm_endpoint: str = Field(
        default="https://inference.do-ai.run/v1/chat/completions",
        validation_alias="CANDIDATE_LLM_ENDPOINT",
        description="Full URL of the candidate LLM chat completions endpoint.",
    )
    candidate_llm_model: str = Field(
        default="llama3.3-70b-instruct",
        validation_alias="CANDIDATE_LLM_MODEL",
        description="Default model applied to the shadow payload when it omits 'model'.",
    )
    candidate_llm_api_key: str = Field(
        default="",
        validation_alias="CANDIDATE_LLM_API_KEY",
        description="Bearer token (DigitalOcean model access key) for the candidate LLM.",
    )
    candidate_llm_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="CANDIDATE_LLM_TIMEOUT_SECONDS",
        description="Per-request timeout for shadow calls to the candidate LLM.",
    )

    # --- Shadow execution (bounded concurrency) ---------------------------
    shadow_queue_size: int = Field(
        default=100,
        ge=1,
        validation_alias="SHADOW_QUEUE_SIZE",
        description="Max pending shadow jobs. Full queue => the job is dropped.",
    )
    shadow_workers: int = Field(
        default=4,
        ge=1,
        validation_alias="SHADOW_WORKERS",
        description="Fixed number of workers draining the shadow queue.",
    )
    shadow_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        validation_alias="SHADOW_PERCENTAGE",
        description="Initial percentage of requests mirrored to the candidate "
        "(runtime-adjustable via PUT /config).",
    )

    # --- Persistence (SQLite) ---------------------------------------------
    sqlite_db_path: str = Field(
        default="shadow_traces.db",
        validation_alias="SQLITE_DB_PATH",
        description="Path to the SQLite database for divergence traces "
        "(created automatically if missing).",
    )

    @property
    def is_production(self) -> bool:
        """Convenience flag for production-only behavior."""

        return self.environment is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Caching keeps the ``.env`` parsing and validation to a single pass and lets
    FastAPI reuse the same object across dependency resolutions. Call
    ``get_settings.cache_clear()`` in tests to force a reload.
    """

    return Settings()
