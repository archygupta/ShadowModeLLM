"""Unit tests for :class:`ShadowPipeline` orchestration + metrics + persistence."""

from __future__ import annotations

import asyncio

import aiosqlite
import httpx

from app.repositories.trace_repository import TraceRepository
from app.services.candidate_llm_service import CandidateLLMService
from app.services.evaluator_service import EvaluatorService
from app.services.metrics_service import MetricsService
from app.services.shadow_pipeline import ShadowPipeline
from tests.conftest import make_mock_client, make_settings


async def _build(candidate_handler, tmp_db_path, **settings_overrides):
    settings = make_settings(**settings_overrides)
    client = make_mock_client(candidate_handler)
    metrics = MetricsService()
    repo = TraceRepository(tmp_db_path)
    await repo.initialize()
    pipeline = ShadowPipeline(
        candidate_service=CandidateLLMService(http_client=client, settings=settings),
        evaluator_service=EvaluatorService(),
        metrics_service=metrics,
        trace_repository=repo,
    )
    return pipeline, metrics, repo, client


def _resolved_future(value):
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    fut.set_result(value)
    return fut


async def test_matching_records_success_and_evaluation_no_persist(tmp_db_path) -> None:
    handler = lambda r: httpx.Response(200, json={"action": "buy"})  # noqa: E731
    pipeline, metrics, repo, client = await _build(handler, tmp_db_path)

    await pipeline.run(
        {"x": 1}, request_id="p-1", primary_future=_resolved_future('{"action": "buy"}')
    )

    snap = await metrics.snapshot()
    assert snap["candidate_success"] == 1
    assert snap["evaluation_runs"] == 1
    assert snap["exact_matches"] == 1
    assert snap["json_parse_failures"] == 0

    # Actions matched => no divergence persisted.
    async with aiosqlite.connect(tmp_db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM traces") as cur:
            (count,) = await cur.fetchone()
    assert count == 0

    await repo.close()
    await client.aclose()


async def test_mismatch_persists_divergence(tmp_db_path) -> None:
    handler = lambda r: httpx.Response(200, json={"action": "sell"})  # noqa: E731
    pipeline, metrics, repo, client = await _build(handler, tmp_db_path)

    await pipeline.run(
        {"x": 1}, request_id="p-2", primary_future=_resolved_future('{"action": "buy"}')
    )

    snap = await metrics.snapshot()
    assert snap["evaluation_runs"] == 1
    assert snap["exact_matches"] == 0

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM traces") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 1
    assert rows[0]["request_id"] == "p-2"

    await repo.close()
    await client.aclose()


async def test_candidate_timeout_records_timeout_metric(tmp_db_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t", request=request)

    pipeline, metrics, repo, client = await _build(handler, tmp_db_path)
    await pipeline.run(
        {"x": 1}, request_id="p-3", primary_future=_resolved_future('{"action": "buy"}')
    )

    snap = await metrics.snapshot()
    assert snap["candidate_timeouts"] == 1
    assert snap["candidate_success"] == 0
    assert snap["evaluation_runs"] == 0  # no evaluation without a candidate body

    await repo.close()
    await client.aclose()


async def test_candidate_connection_error_records_failure(tmp_db_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    pipeline, metrics, repo, client = await _build(handler, tmp_db_path)
    await pipeline.run(
        {"x": 1}, request_id="p-4", primary_future=_resolved_future('{"action": "buy"}')
    )

    snap = await metrics.snapshot()
    assert snap["candidate_failures"] == 1
    assert snap["evaluation_runs"] == 0

    await repo.close()
    await client.aclose()


async def test_disabled_candidate_records_shadow_dropped(tmp_db_path) -> None:
    handler = lambda r: httpx.Response(200, json={"action": "buy"})  # noqa: E731
    pipeline, metrics, repo, client = await _build(
        handler, tmp_db_path, CANDIDATE_LLM_ENABLED=False
    )
    await pipeline.run(
        {"x": 1}, request_id="p-5", primary_future=_resolved_future('{"action": "buy"}')
    )

    snap = await metrics.snapshot()
    assert snap["shadow_dropped"] == 1
    assert snap["candidate_success"] == 0
    assert snap["evaluation_runs"] == 0

    await repo.close()
    await client.aclose()


async def test_unexpected_candidate_error_is_isolated(tmp_db_path, monkeypatch) -> None:
    handler = lambda r: httpx.Response(200, json={"action": "buy"})  # noqa: E731
    pipeline, metrics, repo, client = await _build(handler, tmp_db_path)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("unexpected candidate crash")

    # Force a non-typed error out of invoke() to exercise the catch-all branch.
    monkeypatch.setattr(pipeline._candidate, "invoke", boom)  # noqa: SLF001

    # Must not raise; recorded as a candidate failure.
    await pipeline.run(
        {"x": 1}, request_id="p-7", primary_future=_resolved_future('{"action": "buy"}')
    )
    snap = await metrics.snapshot()
    assert snap["candidate_failures"] == 1
    assert snap["evaluation_runs"] == 0

    await repo.close()
    await client.aclose()


async def test_malformed_candidate_json_counts_parse_failure(tmp_db_path) -> None:
    # Candidate returns non-JSON text; evaluator sees invalid JSON.
    handler = lambda r: httpx.Response(200, text="not-json")  # noqa: E731
    pipeline, metrics, repo, client = await _build(handler, tmp_db_path)
    await pipeline.run(
        {"x": 1}, request_id="p-6", primary_future=_resolved_future('{"action": "buy"}')
    )

    snap = await metrics.snapshot()
    assert snap["evaluation_runs"] == 1
    assert snap["json_parse_failures"] == 1
    assert snap["exact_matches"] == 0

    await repo.close()
    await client.aclose()
