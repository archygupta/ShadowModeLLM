"""Small HTTP helpers shared by the outbound LLM clients.

These live here (rather than being copy-pasted into each service) so the primary
and candidate clients build request headers and parse response bodies in exactly
the same way.
"""

from __future__ import annotations

from typing import Any

import httpx


def bearer_headers(api_key: str) -> dict[str, str]:
    """Return JSON request headers with a bearer ``Authorization``."""

    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def apply_default_model(payload: Any, model: str) -> Any:
    """Return ``payload`` with a default ``model`` filled in when absent.

    Only applies to JSON objects: a caller-supplied ``model`` is preserved, and
    non-object payloads (or an empty ``model``) are returned unchanged. The input
    is never mutated.
    """

    if model and isinstance(payload, dict) and "model" not in payload:
        return {**payload, "model": model}
    return payload


def parse_json_body(response: httpx.Response) -> Any:
    """Return the response decoded as JSON, or a ``{"raw": <text>}`` envelope.

    LLM endpoints normally return JSON, but we never want body parsing to raise
    on unexpected content types; callers get a predictable object either way.
    """

    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def parse_retry_after(response: httpx.Response) -> float | None:
    """Return the ``Retry-After`` delay in seconds, if present and numeric.

    Only the delta-seconds form is honored (the common case for 429/503 from
    inference APIs); the HTTP-date form is ignored and callers fall back to
    their normal backoff.
    """

    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
