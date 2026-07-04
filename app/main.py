"""Application entry point and FastAPI app factory.

Run locally with::

    uvicorn app.main:app --reload

The :func:`create_app` factory keeps construction explicit and test-friendly:
it configures logging, manages the lifecycle of a shared ``httpx.AsyncClient``,
installs request-context middleware, and mounts the API router.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.router import api_router
from app.config import Settings, get_settings
from app.logging import configure_logging, request_id_ctx
from app.repositories.trace_repository import TraceRepository
from app.schemas.common import ErrorResponse
from app.services.metrics_service import MetricsService
from app.services.primary_llm_service import PrimaryLLMError
from app.services.runtime_config import RuntimeConfig
from app.utils.logging_middleware import RequestContextMiddleware
from app.utils.shadow_executor import ShadowExecutor

logger = logging.getLogger(__name__)


async def _primary_llm_error_handler(
    request: Request, exc: PrimaryLLMError
) -> JSONResponse:
    """Map structured primary-LLM failures to a consistent error envelope."""

    request_id = request_id_ctx.get()
    body = ErrorResponse(detail=exc.detail, request_id=request_id)
    return JSONResponse(
        status_code=exc.status_code,
        content=body.model_dump(),
        headers={"X-Request-ID": request_id},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup/shutdown of shared resources.

    Creates a single ``httpx.AsyncClient`` (shared connection pool) on startup
    and closes it cleanly on shutdown.
    """

    settings: Settings = app.state.settings
    logger.info(
        "startup",
        extra={"app": settings.app_name, "environment": settings.environment.value},
    )

    client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    app.state.http_client = client
    app.state.metrics = MetricsService()
    app.state.runtime_config = RuntimeConfig(settings.shadow_percentage)
    app.state.trace_repository = TraceRepository(settings.sqlite_db_path)
    await app.state.trace_repository.initialize()
    app.state.shadow_executor = ShadowExecutor(
        queue_size=settings.shadow_queue_size,
        workers=settings.shadow_workers,
        metrics_service=app.state.metrics,
    )
    await app.state.shadow_executor.start()
    try:
        yield
    finally:
        # Stop shadow workers (cancels in-flight shadow calls), then close the
        # trace DB and HTTP client.
        await app.state.shadow_executor.stop()
        await app.state.trace_repository.close()
        await client.aclose()
        app.state.http_client = None
        logger.info("shutdown", extra={"app": settings.app_name})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a :class:`FastAPI` instance."""

    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    # Stash settings so lifespan and dependencies can read them off app.state.
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(PrimaryLLMError, _primary_llm_error_handler)
    app.include_router(api_router)

    return app


app = create_app()


def run() -> None:
    """Programmatic uvicorn entry point (``python -m app.main``)."""

    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,  # Logging is configured by configure_logging().
    )


if __name__ == "__main__":
    run()
