"""Primary LLM proxy service.

Encapsulates all business logic for forwarding a chat request to the primary
LLM (DigitalOcean Serverless Inference API):

  * request execution against a configured endpoint using a shared
    ``httpx.AsyncClient``,
  * a configurable per-request timeout,
  * retries with exponential backoff (+ jitter, capped, ``Retry-After`` aware)
    for transient failures,
  * structured, HTTP-mappable errors,
  * structured logging of request_id, latency, retries, endpoint and status.

The route layer stays thin: it hands the raw payload to
:meth:`PrimaryLLMService.chat` and returns the result verbatim.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.services.errors import UpstreamLLMError
from app.utils.http import (
    apply_default_model,
    bearer_headers,
    parse_json_body,
    parse_retry_after,
)

logger = logging.getLogger(__name__)

# Upstream status codes worth retrying (transient / server-side).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class PrimaryLLMError(UpstreamLLMError):
    """Base class for structured primary-LLM failures."""

    status_code = 502
    detail = "Primary LLM request failed."


class PrimaryLLMConfigError(PrimaryLLMError):
    """Service is misconfigured (e.g. missing API key)."""

    status_code = 500
    detail = "Primary LLM is not configured correctly."


class PrimaryLLMTimeoutError(PrimaryLLMError):
    """Upstream did not respond within the configured timeout."""

    status_code = 504
    detail = "Primary LLM request timed out."


class PrimaryLLMConnectionError(PrimaryLLMError):
    """Network-level failure reaching the upstream endpoint."""

    status_code = 502
    detail = "Could not reach the primary LLM."


@dataclass(slots=True)
class PrimaryLLMResult:
    """Outcome of a primary LLM call, forwarded verbatim to the client."""

    status_code: int
    content: Any
    latency_ms: float
    retries: int
    endpoint: str
    raw: str = ""


class PrimaryLLMService:
    """Forwards chat payloads to the primary LLM with retries and timeouts."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._http_client = http_client
        self._settings = settings

    async def chat(self, payload: Any, request_id: str) -> PrimaryLLMResult:
        """Forward ``payload`` unchanged to the primary LLM and return its response.

        Retries transient failures (timeouts, connection errors, and retryable
        upstream statuses) up to ``PRIMARY_LLM_MAX_RETRIES`` times using
        exponential backoff. Any HTTP response received (including 4xx and a
        final non-retryable 5xx) is passed through unchanged so the caller sees
        exactly what the primary returned.
        """

        endpoint = self._settings.primary_llm_endpoint
        if not self._settings.primary_llm_api_key:
            logger.error(
                "primary_llm.config_error",
                extra={"request_id": request_id, "endpoint": endpoint},
            )
            raise PrimaryLLMConfigError()

        max_retries = self._settings.primary_llm_max_retries
        timeout = self._settings.primary_llm_timeout_seconds
        headers = bearer_headers(self._settings.primary_llm_api_key)
        request_payload = apply_default_model(payload, self._settings.primary_llm_model)
        started = time.perf_counter()
        last_error: PrimaryLLMError | None = None

        for attempt in range(max_retries + 1):
            retry_after: float | None = None
            try:
                response = await self._http_client.post(
                    endpoint, json=request_payload, headers=headers, timeout=timeout
                )
            except httpx.TimeoutException as exc:
                last_error = PrimaryLLMTimeoutError()
                self._log_attempt_failure(
                    request_id, endpoint, attempt, max_retries,
                    reason="timeout", error=str(exc),
                )
            except httpx.TransportError as exc:
                last_error = PrimaryLLMConnectionError()
                self._log_attempt_failure(
                    request_id, endpoint, attempt, max_retries,
                    reason="transport", error=str(exc),
                )
            else:
                # Retry transient upstream statuses if we have budget left.
                if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                    retry_after = parse_retry_after(response)
                    self._log_attempt_failure(
                        request_id, endpoint, attempt, max_retries,
                        reason="retryable_status",
                        error=f"status={response.status_code}",
                    )
                else:
                    return self._build_result(
                        response, endpoint, started, retries=attempt, request_id=request_id
                    )

            # Sleep before the next attempt (skip after the final attempt).
            if attempt < max_retries:
                await asyncio.sleep(self._backoff_delay(attempt, retry_after))

        # Exhausted all attempts without a usable response.
        error = last_error or PrimaryLLMConnectionError()
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.error(
            "primary_llm.failed",
            extra={
                "request_id": request_id,
                "endpoint": endpoint,
                "status_code": error.status_code,
                "latency_ms": latency_ms,
                "retries": max_retries,
                "error": error.detail,
            },
        )
        raise error

    def _backoff_delay(self, attempt_index: int, retry_after: float | None) -> float:
        """Delay before the next retry.

        Uses exponential backoff with full jitter, capped at
        ``PRIMARY_LLM_BACKOFF_MAX_SECONDS``. When the upstream sent a
        ``Retry-After`` header we respect it as a floor (still capped).
        """

        base = self._settings.primary_llm_backoff_base_seconds
        cap = self._settings.primary_llm_backoff_max_seconds
        backoff = min(cap, base * (2**attempt_index)) + random.uniform(0, base)
        if retry_after is not None:
            backoff = max(backoff, retry_after)
        return min(backoff, cap)

    def _build_result(
        self,
        response: httpx.Response,
        endpoint: str,
        started: float,
        *,
        retries: int,
        request_id: str,
    ) -> PrimaryLLMResult:
        result = PrimaryLLMResult(
            status_code=response.status_code,
            content=parse_json_body(response),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            retries=retries,
            endpoint=endpoint,
            raw=response.text,
        )
        logger.info(
            "primary_llm.completed",
            extra={
                "request_id": request_id,
                "endpoint": endpoint,
                "status_code": result.status_code,
                "latency_ms": result.latency_ms,
                "retries": result.retries,
            },
        )
        return result

    @staticmethod
    def _log_attempt_failure(
        request_id: str,
        endpoint: str,
        attempt: int,
        max_retries: int,
        *,
        reason: str,
        error: str,
    ) -> None:
        logger.warning(
            "primary_llm.attempt_failed",
            extra={
                "request_id": request_id,
                "endpoint": endpoint,
                "attempt": attempt + 1,
                "max_attempts": max_retries + 1,
                "reason": reason,
                "error": error,
            },
        )
