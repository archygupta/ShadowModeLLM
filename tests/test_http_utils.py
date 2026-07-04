"""Unit tests for the shared HTTP helpers."""

from __future__ import annotations

import httpx

from app.utils.http import (
    apply_default_model,
    bearer_headers,
    parse_json_body,
    parse_retry_after,
)


def test_apply_default_model_fills_when_absent() -> None:
    result = apply_default_model({"messages": []}, "m-1")
    assert result == {"messages": [], "model": "m-1"}


def test_apply_default_model_preserves_caller_value() -> None:
    result = apply_default_model({"model": "caller", "messages": []}, "m-1")
    assert result["model"] == "caller"


def test_apply_default_model_does_not_mutate_input() -> None:
    original = {"messages": []}
    apply_default_model(original, "m-1")
    assert "model" not in original


def test_apply_default_model_passthrough_non_dict() -> None:
    assert apply_default_model([1, 2, 3], "m-1") == [1, 2, 3]


def test_apply_default_model_passthrough_empty_model() -> None:
    assert apply_default_model({"a": 1}, "") == {"a": 1}


def test_bearer_headers() -> None:
    headers = bearer_headers("secret")
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"


def test_parse_json_body_valid() -> None:
    resp = httpx.Response(200, json={"action": "buy"})
    assert parse_json_body(resp) == {"action": "buy"}


def test_parse_json_body_invalid_wraps_raw() -> None:
    resp = httpx.Response(200, text="not-json")
    assert parse_json_body(resp) == {"raw": "not-json"}


def test_parse_retry_after_numeric() -> None:
    resp = httpx.Response(429, headers={"Retry-After": "3"})
    assert parse_retry_after(resp) == 3.0


def test_parse_retry_after_missing() -> None:
    resp = httpx.Response(429)
    assert parse_retry_after(resp) is None


def test_parse_retry_after_non_numeric_ignored() -> None:
    # HTTP-date form is intentionally not supported.
    resp = httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert parse_retry_after(resp) is None


def test_parse_retry_after_negative_ignored() -> None:
    resp = httpx.Response(503, headers={"Retry-After": "-5"})
    assert parse_retry_after(resp) is None
