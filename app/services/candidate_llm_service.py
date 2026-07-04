"""Candidate LLM service (shadow mode).

Sends the *same* payload the primary received to a candidate model, purely for
observation. :meth:`CandidateLLMService.invoke` performs a single call with a
configurable timeout and raises structured exceptions on failure.

Isolation from the primary path and metric recording are handled by the
detached :class:`~app.services.shadow_pipeline.ShadowPipeline`, which catches
these exceptions. No retries are performed for the candidate; shadow traffic is
best-effort.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.services.errors import UpstreamLLMError
from app.utils.http import apply_default_model, bearer_headers, parse_json_body

logger = logging.getLogger(__name__)


class CandidateLLMError(UpstreamLLMError):
    """Base class for structured candidate-LLM failures.

    Candidate failures are observational: they are caught by the shadow
    pipeline and translated into metrics, never surfaced to the client.
    """

    detail = "Candidate LLM request failed."


class CandidateLLMConfigError(CandidateLLMError):
    """Candidate is not configured correctly (e.g. missing API key)."""

    detail = "Candidate LLM is not configured correctly."


class CandidateLLMTimeoutError(CandidateLLMError):
    """Candidate did not respond within the configured timeout."""

    detail = "Candidate LLM request timed out."


class CandidateLLMConnectionError(CandidateLLMError):
    """Network-level failure reaching the candidate endpoint."""

    detail = "Could not reach the candidate LLM."


@dataclass(slots=True)
class CandidateLLMResult:
    """Outcome of a candidate call (observational only)."""

    status_code: int
    latency_ms: float
    endpoint: str
    content: Any = None
    raw: str = ""


class CandidateLLMService:
    """Invokes the candidate LLM for shadow-mode observation."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._http_client = http_client
        self._settings = settings

    @property
    def enabled(self) -> bool:
        """Whether shadow calls should run (toggled + fully configured)."""

        return bool(
            self._settings.candidate_llm_enabled
            and self._settings.candidate_llm_endpoint
            and self._settings.candidate_llm_api_key
        )

    async def invoke(self, payload: Any, request_id: str) -> CandidateLLMResult:
        """Send ``payload`` to the candidate endpoint once, honoring the timeout.

        Raises a :class:`CandidateLLMError` subclass on failure.
        """

        endpoint = self._settings.candidate_llm_endpoint
        timeout = self._settings.candidate_llm_timeout_seconds

        if not self._settings.candidate_llm_api_key:
            raise CandidateLLMConfigError()

        request_payload = apply_default_model(
            payload, self._settings.candidate_llm_model
        )
        started = time.perf_counter()
        try:
            response = await self._http_client.post(
                endpoint,
                json=request_payload,
                headers=bearer_headers(self._settings.candidate_llm_api_key),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "candidate_llm.timeout",
                extra={
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "timeout_seconds": timeout,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error": str(exc),
                },
            )
            raise CandidateLLMTimeoutError() from exc
        except httpx.TransportError as exc:
            logger.warning(
                "candidate_llm.connection_error",
                extra={
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error": str(exc),
                },
            )
            raise CandidateLLMConnectionError() from exc

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "candidate_llm.completed",
            extra={
                "request_id": request_id,
                "endpoint": endpoint,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "timeout_seconds": timeout,
            },
        )
        return CandidateLLMResult(
            status_code=response.status_code,
            latency_ms=latency_ms,
            endpoint=endpoint,
            content=parse_json_body(response),
            raw=response.text,
        )
