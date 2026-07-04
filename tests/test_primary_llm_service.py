"""Unit tests for :class:`PrimaryLLMService` (retries, timeouts, errors)."""

from __future__ import annotations

import httpx
import pytest

from app.services.primary_llm_service import (
    PrimaryLLMConfigError,
    PrimaryLLMConnectionError,
    PrimaryLLMService,
    PrimaryLLMTimeoutError,
)
from tests.conftest import make_mock_client, make_settings


async def test_primary_success_forwards_payload_and_returns_response() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"action": "buy"})

    settings = make_settings()
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    result = await service.chat({"prompt": "hi"}, request_id="req-1")

    assert result.status_code == 200
    assert result.content == {"action": "buy"}
    assert result.retries == 0
    assert result.latency_ms >= 0.0
    assert result.raw == '{"action": "buy"}' or result.content == {"action": "buy"}
    # Payload forwarded unchanged, with bearer auth.
    assert b'"prompt"' in seen["body"]  # type: ignore[operator]
    assert seen["auth"] == "Bearer primary-key"
    await client.aclose()


async def test_primary_injects_default_model_when_absent() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"action": "buy"})

    settings = make_settings(PRIMARY_LLM_MODEL="default-model")
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    await service.chat({"messages": []}, request_id="req-model")
    assert seen["body"] == {"messages": [], "model": "default-model"}  # type: ignore[comparison-overlap]
    await client.aclose()


async def test_primary_preserves_caller_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"action": "buy"})

    settings = make_settings(PRIMARY_LLM_MODEL="default-model")
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    await service.chat({"model": "caller-model"}, request_id="req-model2")
    assert seen["body"] == {"model": "caller-model"}  # type: ignore[comparison-overlap]
    await client.aclose()


async def test_primary_missing_api_key_raises_config_error() -> None:
    settings = make_settings(PRIMARY_LLM_API_KEY="")
    client = make_mock_client(lambda r: httpx.Response(200, json={}))
    service = PrimaryLLMService(http_client=client, settings=settings)

    with pytest.raises(PrimaryLLMConfigError) as exc:
        await service.chat({"x": 1}, request_id="req-2")
    assert exc.value.status_code == 500
    await client.aclose()


async def test_primary_timeout_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    settings = make_settings(PRIMARY_LLM_MAX_RETRIES=0)
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    with pytest.raises(PrimaryLLMTimeoutError) as exc:
        await service.chat({"x": 1}, request_id="req-3")
    assert exc.value.status_code == 504
    await client.aclose()


async def test_primary_connection_error_raises_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = make_settings(PRIMARY_LLM_MAX_RETRIES=0)
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    with pytest.raises(PrimaryLLMConnectionError) as exc:
        await service.chat({"x": 1}, request_id="req-4")
    assert exc.value.status_code == 502
    await client.aclose()


async def test_primary_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"action": "hold"})

    settings = make_settings(PRIMARY_LLM_MAX_RETRIES=2)
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    result = await service.chat({"x": 1}, request_id="req-5")

    assert calls["n"] == 2
    assert result.status_code == 200
    assert result.retries == 1  # one retry consumed
    await client.aclose()


async def test_primary_exhausts_retries_on_timeout() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("t", request=request)

    settings = make_settings(PRIMARY_LLM_MAX_RETRIES=2)
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    with pytest.raises(PrimaryLLMTimeoutError):
        await service.chat({"x": 1}, request_id="req-6")
    assert calls["n"] == 3  # initial + 2 retries
    await client.aclose()


async def test_primary_honors_retry_after_header(monkeypatch) -> None:
    import app.services.primary_llm_service as mod

    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"}, json={"e": 1})
        return httpx.Response(200, json={"action": "buy"})

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    # Deterministic jitter + captured sleeps.
    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

    settings = make_settings(
        PRIMARY_LLM_MAX_RETRIES=2,
        PRIMARY_LLM_BACKOFF_BASE_SECONDS=0.5,
        PRIMARY_LLM_BACKOFF_MAX_SECONDS=8.0,
    )
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    result = await service.chat({"x": 1}, request_id="req-ra")
    assert result.status_code == 200
    # Retry-After (5s) dominates the base backoff (~0.5s), and is under the cap.
    assert slept == [5.0]
    await client.aclose()


async def test_primary_backoff_is_capped(monkeypatch) -> None:
    import app.services.primary_llm_service as mod

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t", request=request)

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

    settings = make_settings(
        PRIMARY_LLM_MAX_RETRIES=5,
        PRIMARY_LLM_BACKOFF_BASE_SECONDS=1.0,
        PRIMARY_LLM_BACKOFF_MAX_SECONDS=4.0,
    )
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    with pytest.raises(PrimaryLLMTimeoutError):
        await service.chat({"x": 1}, request_id="req-cap")
    # Exponential (1,2,4,8,16) clamped to the 4s cap.
    assert slept == [1.0, 2.0, 4.0, 4.0, 4.0]
    await client.aclose()


async def test_primary_passes_through_non_retryable_4xx() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad"})

    settings = make_settings(PRIMARY_LLM_MAX_RETRIES=2)
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    result = await service.chat({"x": 1}, request_id="req-7")
    assert result.status_code == 400
    assert result.retries == 0
    await client.aclose()


async def test_primary_wraps_malformed_json_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    settings = make_settings()
    client = make_mock_client(handler)
    service = PrimaryLLMService(http_client=client, settings=settings)

    result = await service.chat({"x": 1}, request_id="req-8")
    assert result.content == {"raw": "not-json"}
    assert result.raw == "not-json"
    await client.aclose()
