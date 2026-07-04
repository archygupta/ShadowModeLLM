"""Shadow-mode orchestration.

Composes the candidate call and the evaluator into a single detached background
coroutine. Responsibilities:

  * run the candidate call (which starts immediately, concurrently with the
    primary) and record its outcome metrics,
  * wait for the primary response (handed over via a future so the client is
    never blocked),
  * invoke :class:`~app.services.evaluator_service.EvaluatorService` once the
    candidate finishes, and record evaluation metrics.

This orchestrator never raises: candidate errors are caught and translated into
metrics, and everything else is wrapped defensively so a detached task can't
crash. Isolation of the primary path is therefore fully preserved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.logging import request_id_ctx
from app.repositories.trace_repository import TraceRepository
from app.services.candidate_llm_service import (
    CandidateLLMError,
    CandidateLLMService,
    CandidateLLMTimeoutError,
)
from app.services.evaluator_service import EvaluatorService
from app.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)


class ShadowPipeline:
    """Runs the candidate call then the evaluator, off the request path."""

    def __init__(
        self,
        candidate_service: CandidateLLMService,
        evaluator_service: EvaluatorService,
        metrics_service: MetricsService,
        trace_repository: TraceRepository,
    ) -> None:
        self._candidate = candidate_service
        self._evaluator = evaluator_service
        self._metrics = metrics_service
        self._trace_repository = trace_repository

    async def run(
        self,
        payload: Any,
        request_id: str,
        primary_future: "asyncio.Future[Any]",
    ) -> None:
        """Execute the shadow call and evaluate against the primary response.

        ``primary_future`` resolves to the primary response content (or ``None``
        if the primary failed). This coroutine is scheduled detached and is
        never awaited by the request handler.
        """

        # Bind the correlation id so every log line emitted by this detached
        # worker (candidate call, evaluation, persistence) shares the request id.
        token = request_id_ctx.set(request_id)
        try:
            candidate_raw = await self._run_candidate(payload, request_id)
            if candidate_raw is _NO_CANDIDATE:
                # Shadow skipped or candidate failed: nothing to evaluate.
                return

            # primary_future carries the raw primary response body (or None).
            primary_raw = await primary_future
            evaluation = self._evaluator.evaluate(primary_raw, candidate_raw)
            await self._metrics.record_evaluation(
                exact_match=evaluation.exact_match, json_valid=evaluation.json_valid
            )
            logger.info(
                "evaluation.completed",
                extra={
                    "json_valid": evaluation.json_valid,
                    "primary_action": evaluation.primary_action,
                    "candidate_action": evaluation.candidate_action,
                    "exact_match": evaluation.exact_match,
                },
            )

            # Persist divergences for offline inspection (off the request path).
            if evaluation.primary_action != evaluation.candidate_action:
                await self._trace_repository.save_trace(
                    request_id=request_id,
                    primary_response=primary_raw,
                    candidate_response=candidate_raw,
                    evaluation_result=evaluation.model_dump(),
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - detached task must never crash
            logger.exception("shadow_pipeline.unexpected_error")
        finally:
            request_id_ctx.reset(token)

    async def _run_candidate(self, payload: Any, request_id: str) -> Any:
        """Invoke the candidate, record outcome metrics, and return its raw body.

        Returns the sentinel :data:`_NO_CANDIDATE` when the shadow was skipped or
        the call failed, so the caller can decide not to evaluate.
        """

        if not self._candidate.enabled:
            await self._metrics.record_shadow_dropped()
            logger.debug(
                "candidate_llm.skipped",
                extra={"reason": "disabled_or_unconfigured"},
            )
            return _NO_CANDIDATE

        try:
            result = await self._candidate.invoke(payload, request_id=request_id)
        except CandidateLLMTimeoutError:
            await self._metrics.record_candidate_timeout()
            return _NO_CANDIDATE
        except CandidateLLMError as exc:
            await self._metrics.record_candidate_failure()
            logger.warning("candidate_llm.failed", extra={"error": exc.detail})
            return _NO_CANDIDATE
        except Exception:  # noqa: BLE001 - isolate unexpected candidate errors
            await self._metrics.record_candidate_failure()
            logger.exception("candidate_llm.unexpected_error")
            return _NO_CANDIDATE

        await self._metrics.record_candidate_success(result.latency_ms)
        return result.raw


# Sentinel distinguishing "no candidate response" from a legitimate ``None`` body.
_NO_CANDIDATE: Any = object()
