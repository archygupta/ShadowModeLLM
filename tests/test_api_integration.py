"""End-to-end API tests driving the real app through ASGITransport.

The primary and candidate LLMs are mocked with httpx.MockTransport; the shadow
executor, metrics service, and SQLite repository all run for real.
"""

from __future__ import annotations

import json

import aiosqlite
import httpx

from tests.conftest import (
    build_harness,
    drain_shadow,
    json_handler,
    make_settings,
    text_handler,
    timeout_handler,
)


async def test_health_and_readiness() -> None:
    settings = make_settings(SQLITE_DB_PATH=":memory:")
    async with build_harness(
        settings, primary_handler=json_handler({"action": "buy"})
    ) as (_app, client):
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["app"] == settings.app_name

        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        # All lifespan-created resources report ready.
        assert body["checks"] == {
            "http_client": True,
            "metrics": True,
            "shadow_executor": True,
            "trace_repository": True,
        }


async def test_chat_primary_success_returns_response_and_updates_metrics(
    tmp_db_path: str,
) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=json_handler({"action": "buy"}),
    ) as (app, client):
        resp = await client.post("/v1/chat", json={"prompt": "hello"})
        assert resp.status_code == 200
        assert resp.json() == {"action": "buy"}
        # Set once by the middleware (no duplicate from the handler).
        assert "X-Request-ID" in resp.headers
        assert "," not in resp.headers["X-Request-ID"]
        assert "X-Primary-Latency-Ms" in resp.headers

        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["total_requests"] == 1
        assert metrics["primary_success"] == 1
        assert metrics["candidate_success"] == 1
        assert metrics["evaluation_runs"] == 1
        assert metrics["exact_matches"] == 1
        assert metrics["match_rate_percent"] == 100.0


async def test_chat_primary_timeout_returns_504(tmp_db_path: str) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path, PRIMARY_LLM_MAX_RETRIES=0)
    async with build_harness(
        settings,
        primary_handler=timeout_handler(),
        candidate_handler=json_handler({"action": "buy"}),
    ) as (app, client):
        resp = await client.post("/v1/chat", json={"prompt": "hi"})
        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()

        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["total_requests"] == 1
        assert metrics["primary_failures"] == 1
        assert metrics["primary_success"] == 0


async def test_chat_candidate_timeout_does_not_affect_primary(
    tmp_db_path: str,
) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=timeout_handler(),
    ) as (app, client):
        resp = await client.post("/v1/chat", json={"prompt": "hi"})
        # Primary still succeeds despite candidate timing out.
        assert resp.status_code == 200
        assert resp.json() == {"action": "buy"}

        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["primary_success"] == 1
        assert metrics["candidate_timeouts"] == 1
        assert metrics["evaluation_runs"] == 0


async def test_divergence_is_persisted_to_sqlite(tmp_db_path: str) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=json_handler({"action": "sell"}),
    ) as (app, client):
        resp = await client.post("/v1/chat", json={"prompt": "hi"})
        assert resp.status_code == 200
        # The X-Request-ID response header may appear twice (middleware + handler),
        # which httpx joins with ", "; normalize to the underlying id.
        header_ids = {p.strip() for p in resp.headers["X-Request-ID"].split(",")}

        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["evaluation_runs"] == 1
        assert metrics["exact_matches"] == 0

    # Inspect the persisted divergence after shutdown.
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM traces") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 1
    assert rows[0]["request_id"] in header_ids
    assert json.loads(rows[0]["primary_response"]) == {"action": "buy"}
    assert json.loads(rows[0]["candidate_response"]) == {"action": "sell"}
    assert json.loads(rows[0]["evaluation_result"])["exact_match"] is False


async def test_matching_actions_are_not_persisted(tmp_db_path: str) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=json_handler({"action": "buy"}),
    ) as (app, client):
        await client.post("/v1/chat", json={"prompt": "hi"})
        await drain_shadow(app)

    async with aiosqlite.connect(tmp_db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM traces") as cur:
            (count,) = await cur.fetchone()
    assert count == 0


async def test_shadow_percentage_zero_skips_candidate_but_runs_primary(
    tmp_db_path: str,
) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path, SHADOW_PERCENTAGE=0)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=json_handler({"action": "sell"}),
    ) as (app, client):
        for _ in range(10):
            resp = await client.post("/v1/chat", json={"prompt": "hi"})
            assert resp.status_code == 200

        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["total_requests"] == 10
        assert metrics["primary_success"] == 10  # primary always runs
        assert metrics["evaluation_runs"] == 0  # nothing mirrored
        assert metrics["candidate_success"] == 0


async def test_put_config_updates_routing_at_runtime(tmp_db_path: str) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path, SHADOW_PERCENTAGE=100)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=json_handler({"action": "buy"}),
    ) as (app, client):
        assert (await client.get("/config")).json() == {"shadow_percentage": 100}

        # Turn mirroring off at runtime.
        put = await client.put("/config", json={"shadow_percentage": 0})
        assert put.status_code == 200
        assert put.json() == {"shadow_percentage": 0}

        for _ in range(5):
            await client.post("/v1/chat", json={"prompt": "hi"})
        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["evaluation_runs"] == 0  # updated value took effect

        # Confirm GET reflects the new value.
        assert (await client.get("/config")).json() == {"shadow_percentage": 0}


async def test_put_config_validation_rejects_out_of_range(tmp_db_path: str) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path)
    async with build_harness(
        settings, primary_handler=json_handler({"action": "buy"})
    ) as (_app, client):
        assert (await client.put("/config", json={"shadow_percentage": 101})).status_code == 422
        assert (await client.put("/config", json={"shadow_percentage": -1})).status_code == 422
        assert (await client.put("/config", json={})).status_code == 422


async def test_malformed_json_from_llms_counts_parse_failure(
    tmp_db_path: str,
) -> None:
    settings = make_settings(SQLITE_DB_PATH=tmp_db_path)
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=text_handler("totally-not-json"),
    ) as (app, client):
        resp = await client.post("/v1/chat", json={"prompt": "hi"})
        assert resp.status_code == 200

        await drain_shadow(app)
        metrics = (await client.get("/metrics")).json()
        assert metrics["evaluation_runs"] == 1
        assert metrics["json_parse_failures"] == 1


async def test_queue_full_drops_shadow_under_load(tmp_db_path: str) -> None:
    # Tiny queue + single worker + a slow candidate so the queue saturates and
    # excess shadow jobs are dropped (recorded as shadow_dropped).
    import asyncio

    async def slow_candidate(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"action": "buy"})

    settings = make_settings(
        SQLITE_DB_PATH=tmp_db_path, SHADOW_QUEUE_SIZE=1, SHADOW_WORKERS=1
    )
    async with build_harness(
        settings,
        primary_handler=json_handler({"action": "buy"}),
        candidate_handler=slow_candidate,
    ) as (app, client):
        for _ in range(20):
            resp = await client.post("/v1/chat", json={"prompt": "hi"})
            assert resp.status_code == 200  # primary never blocked/affected

        metrics = (await client.get("/metrics")).json()
        assert metrics["total_requests"] == 20
        assert metrics["primary_success"] == 20
        # With a 1-slot queue and a slow worker, most shadows must be dropped.
        assert metrics["shadow_dropped"] > 0
