"""Health and readiness endpoints for liveness/readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app import __version__
from app.api.deps import SettingsDep
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    """Return basic liveness information; always cheap and dependency-free."""

    return HealthResponse(
        app=settings.app_name,
        environment=settings.environment.value,
        version=__version__,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Report whether core dependencies are initialized and ready.

    Verifies each resource created during the app lifespan. Returns HTTP 503
    when any check fails so orchestrators hold traffic until the app is ready.
    """

    state = request.app.state
    executor = getattr(state, "shadow_executor", None)
    trace_repo = getattr(state, "trace_repository", None)
    checks: dict[str, bool] = {
        "http_client": getattr(state, "http_client", None) is not None,
        "metrics": getattr(state, "metrics", None) is not None,
        "shadow_executor": executor is not None and executor.is_running,
        "trace_repository": trace_repo is not None and trace_repo.is_ready,
    }

    is_ready = all(checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        checks=checks,
    )
