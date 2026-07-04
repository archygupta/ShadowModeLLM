"""Unit tests for :class:`CandidateLLMService` (shadow-mode calls)."""

from __future__ import annotations

import httpx
import pytest

from app.services.candidate_llm_service import (
    CandidateLLMConfigError,
    CandidateLLMConnectionError,
    CandidateLLMService,
    CandidateLLMTimeoutError,
)
from tests.conftest import make_mock_client, make_settings


async def test_candidate_success_returns_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer candidate-key"
        return httpx.Response(200, json={"action": "sell"})

    settings = make_settings()
    client = make_mock_client(handler)
    service = CandidateLLMService(http_client=client, settings=settings)

    result = await service.invoke({"x": 1}, request_id="c-1")
    assert result.status_code == 200
    assert result.content == {"action": "sell"}
    assert result.latency_ms >= 0.0
    await client.aclose()


@pytest.mark.parametrize(
    "overrides,enabled",
    [
        ({}, True),
        ({"CANDIDATE_LLM_ENABLED": False}, False),
        ({"CANDIDATE_LLM_API_KEY": ""}, False),
        ({"CANDIDATE_LLM_ENDPOINT": ""}, False),
    ],
)
async def test_candidate_enabled_flag(overrides: dict, enabled: bool) -> None:
    settings = make_settings(**overrides)
    client = make_mock_client(lambda r: httpx.Response(200, json={}))
    service = CandidateLLMService(http_client=client, settings=settings)
    assert service.enabled is enabled
    await client.aclose()


async def test_candidate_timeout_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t", request=request)

    settings = make_settings()
    client = make_mock_client(handler)
    service = CandidateLLMService(http_client=client, settings=settings)

    with pytest.raises(CandidateLLMTimeoutError):
        await service.invoke({"x": 1}, request_id="c-2")
    await client.aclose()


async def test_candidate_connection_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = make_settings()
    client = make_mock_client(handler)
    service = CandidateLLMService(http_client=client, settings=settings)

    with pytest.raises(CandidateLLMConnectionError):
        await service.invoke({"x": 1}, request_id="c-3")
    await client.aclose()


async def test_candidate_missing_key_raises_config_error() -> None:
    # No key => enabled is False, but invoke() must still guard defensively.
    settings = make_settings(CANDIDATE_LLM_API_KEY="")
    client = make_mock_client(lambda r: httpx.Response(200, json={}))
    service = CandidateLLMService(http_client=client, settings=settings)

    with pytest.raises(CandidateLLMConfigError):
        await service.invoke({"x": 1}, request_id="c-4")
    await client.aclose()


async def test_candidate_wraps_malformed_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<<garbage>>")

    settings = make_settings()
    client = make_mock_client(handler)
    service = CandidateLLMService(http_client=client, settings=settings)

    result = await service.invoke({"x": 1}, request_id="c-5")
    assert result.content == {"raw": "<<garbage>>"}
    assert result.raw == "<<garbage>>"
    await client.aclose()
