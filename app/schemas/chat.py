"""Schemas for the ``POST /v1/chat`` primary proxy endpoint.

The request body is intentionally *unmodeled*: the endpoint accepts arbitrary
JSON and forwards it unchanged to the primary LLM. The successful response is
likewise the primary's response passed through verbatim. Only the error
envelope is modeled, so it can be advertised in the OpenAPI schema.
"""

from __future__ import annotations

from app.schemas.common import ErrorResponse

# The chat proxy surfaces failures using the shared error envelope.
ChatErrorResponse = ErrorResponse

__all__ = ["ChatErrorResponse"]
