"""FastAPI dependency providers (the dependency-injection seams).

Routes depend on these small functions instead of reaching into globals. This
keeps handlers thin and makes everything trivial to override in tests via
``app.dependency_overrides``.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.repositories.trace_repository import TraceRepository
from app.services.candidate_llm_service import CandidateLLMService
from app.services.evaluator_service import EvaluatorService
from app.services.metrics_service import MetricsService
from app.services.primary_llm_service import PrimaryLLMService
from app.services.runtime_config import RuntimeConfig
from app.services.shadow_pipeline import ShadowPipeline
from app.utils.shadow_executor import ShadowExecutor


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the process-wide :class:`httpx.AsyncClient`.

    The client is created during application startup (see ``main.lifespan``)
    and stored on ``app.state`` so a single connection pool is shared.
    """

    client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    if client is None:  # pragma: no cover - indicates a startup wiring bug
        raise RuntimeError("HTTP client is not initialized; check app lifespan setup.")
    return client


SettingsDep = Annotated[Settings, Depends(get_settings)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


def get_evaluator_service() -> EvaluatorService:
    """Construct the :class:`EvaluatorService` (pure, no dependencies)."""

    return EvaluatorService()


EvaluatorServiceDep = Annotated[EvaluatorService, Depends(get_evaluator_service)]


def get_primary_llm_service(
    http_client: HttpClientDep, settings: SettingsDep
) -> PrimaryLLMService:
    """Construct the :class:`PrimaryLLMService` with its dependencies."""

    return PrimaryLLMService(http_client=http_client, settings=settings)


PrimaryLLMServiceDep = Annotated[PrimaryLLMService, Depends(get_primary_llm_service)]


def get_candidate_llm_service(
    http_client: HttpClientDep, settings: SettingsDep
) -> CandidateLLMService:
    """Construct the :class:`CandidateLLMService` with its dependencies."""

    return CandidateLLMService(http_client=http_client, settings=settings)


CandidateLLMServiceDep = Annotated[
    CandidateLLMService, Depends(get_candidate_llm_service)
]


def get_metrics_service(request: Request) -> MetricsService:
    """Return the process-wide :class:`MetricsService`.

    Created during application startup and stored on ``app.state`` so counters
    persist across requests for the process lifetime.
    """

    metrics: MetricsService | None = getattr(request.app.state, "metrics", None)
    if metrics is None:  # pragma: no cover - indicates a startup wiring bug
        raise RuntimeError("MetricsService is not initialized; check app lifespan setup.")
    return metrics


MetricsServiceDep = Annotated[MetricsService, Depends(get_metrics_service)]


def get_runtime_config(request: Request) -> RuntimeConfig:
    """Return the process-wide :class:`RuntimeConfig`.

    Created during application startup and stored on ``app.state`` so runtime
    updates (e.g. via ``PUT /config``) are visible to all subsequent requests.
    """

    config: RuntimeConfig | None = getattr(request.app.state, "runtime_config", None)
    if config is None:  # pragma: no cover - indicates a startup wiring bug
        raise RuntimeError("RuntimeConfig is not initialized; check app lifespan setup.")
    return config


RuntimeConfigDep = Annotated[RuntimeConfig, Depends(get_runtime_config)]


def get_trace_repository(request: Request) -> TraceRepository:
    """Return the process-wide :class:`TraceRepository`.

    Created and initialized during application startup and stored on
    ``app.state``.
    """

    repo: TraceRepository | None = getattr(
        request.app.state, "trace_repository", None
    )
    if repo is None:  # pragma: no cover - indicates a startup wiring bug
        raise RuntimeError(
            "TraceRepository is not initialized; check app lifespan setup."
        )
    return repo


TraceRepositoryDep = Annotated[TraceRepository, Depends(get_trace_repository)]


def get_shadow_pipeline(
    candidate_service: CandidateLLMServiceDep,
    evaluator_service: EvaluatorServiceDep,
    metrics_service: MetricsServiceDep,
    trace_repository: TraceRepositoryDep,
) -> ShadowPipeline:
    """Construct the :class:`ShadowPipeline` orchestrator."""

    return ShadowPipeline(
        candidate_service=candidate_service,
        evaluator_service=evaluator_service,
        metrics_service=metrics_service,
        trace_repository=trace_repository,
    )


ShadowPipelineDep = Annotated[ShadowPipeline, Depends(get_shadow_pipeline)]


def get_shadow_executor(request: Request) -> ShadowExecutor:
    """Return the process-wide :class:`ShadowExecutor`.

    Created and started during application startup and stored on ``app.state``.
    """

    executor: ShadowExecutor | None = getattr(
        request.app.state, "shadow_executor", None
    )
    if executor is None:  # pragma: no cover - indicates a startup wiring bug
        raise RuntimeError(
            "ShadowExecutor is not initialized; check app lifespan setup."
        )
    return executor


ShadowExecutorDep = Annotated[ShadowExecutor, Depends(get_shadow_executor)]
