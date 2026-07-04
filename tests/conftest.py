"""Shared test fixtures and helpers.

External HTTP is mocked with :class:`httpx.MockTransport`; the FastAPI app is
driven in-process with :class:`httpx.ASGITransport` while its real lifespan runs
(so the metrics service, shadow executor, and SQLite repository are all wired up
exactly as in production).
"""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator, Callable

import httpx
import pytest

from app.api import deps
from app.config import Settings
from app.main import create_app
from app.services.candidate_llm_service import CandidateLLMService
from app.services.evaluator_service import EvaluatorService
from app.services.primary_llm_service import PrimaryLLMService
from app.services.shadow_pipeline import ShadowPipeline

PRIMARY_ENDPOINT = "https://primary.test/v1/chat/completions"
CANDIDATE_ENDPOINT = "https://candidate.test/v1/chat/completions"

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: Any) -> Settings:
    """Build deterministic :class:`Settings`, ignoring any repo ``.env``.

    Defaults configure both LLMs with fake keys/endpoints and disable retry
    backoff so tests stay fast. Override via keyword (env-alias) args.
    """

    base: dict[str, Any] = {
        "PRIMARY_LLM_API_KEY": "primary-key",
        "PRIMARY_LLM_ENDPOINT": PRIMARY_ENDPOINT,
        "PRIMARY_LLM_MAX_RETRIES": 0,
        "PRIMARY_LLM_BACKOFF_BASE_SECONDS": 0.01,
        "CANDIDATE_LLM_API_KEY": "candidate-key",
        "CANDIDATE_LLM_ENDPOINT": CANDIDATE_ENDPOINT,
        "CANDIDATE_LLM_ENABLED": True,
        "SHADOW_PERCENTAGE": 100,
    }
    base.update(overrides)
    # _env_file=None => never read the on-disk .env, so tests are hermetic.
    return Settings(_env_file=None, **base)


def make_mock_client(handler: Handler) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` whose requests are served by ``handler``."""

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_handler(payload: Any, status_code: int = 200) -> Handler:
    """Handler that always returns ``payload`` as JSON with ``status_code``."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return _handler


def text_handler(body: str, status_code: int = 200) -> Handler:
    """Handler returning raw ``body`` text (used for malformed-JSON cases)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=body)

    return _handler


def timeout_handler() -> Handler:
    """Handler that raises a timeout as if the upstream never responded."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("mock timeout", request=request)

    return _handler


@contextlib.asynccontextmanager
async def build_harness(
    settings: Settings,
    *,
    primary_handler: Handler,
    candidate_handler: Handler | None = None,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """Yield ``(app, client)`` with the real lifespan running.

    ``primary_handler`` serves the primary LLM; ``candidate_handler`` (when
    given) serves the candidate through an overridden shadow pipeline that still
    uses the app's real metrics service and trace repository.
    """

    app = create_app(settings=settings)

    primary_client = make_mock_client(primary_handler)
    app.dependency_overrides[deps.get_primary_llm_service] = (
        lambda: PrimaryLLMService(http_client=primary_client, settings=settings)
    )

    candidate_client: httpx.AsyncClient | None = None
    if candidate_handler is not None:
        candidate_client = make_mock_client(candidate_handler)

        def _pipeline_override() -> ShadowPipeline:
            # app.state is populated once the lifespan below has started.
            return ShadowPipeline(
                candidate_service=CandidateLLMService(
                    http_client=candidate_client, settings=settings
                ),
                evaluator_service=EvaluatorService(),
                metrics_service=app.state.metrics,
                trace_repository=app.state.trace_repository,
            )

        app.dependency_overrides[deps.get_shadow_pipeline] = _pipeline_override

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            try:
                yield app, client
            finally:
                await primary_client.aclose()
                if candidate_client is not None:
                    await candidate_client.aclose()


async def drain_shadow(app: Any, *, timeout: float = 2.0) -> None:
    """Wait until all queued/in-flight shadow jobs have completed."""

    import asyncio

    executor = app.state.shadow_executor
    deadline = asyncio.get_running_loop().time() + timeout
    while executor.pending > 0:
        if asyncio.get_running_loop().time() > deadline:
            break
        await asyncio.sleep(0.005)
    # Give the worker a beat to finish the job it just dequeued (+ persistence).
    await asyncio.sleep(0.05)


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    return make_settings


@pytest.fixture
def tmp_db_path(tmp_path: Any) -> str:
    return str(tmp_path / "traces.db")
