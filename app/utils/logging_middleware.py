"""HTTP middleware that adds request correlation ids and access logging.

For each inbound request it:
  * reuses an incoming ``X-Request-ID`` header or generates a new UUID,
  * binds that id to :data:`app.logging.request_id_ctx` so every log line
    emitted downstream is correlated,
  * logs request start/completion with method, path, status, and duration,
  * echoes the id back on the response via the ``X-Request-ID`` header.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging import request_id_ctx

_REQUEST_ID_HEADER = "x-request-id"
logger = logging.getLogger("app.access")


class RequestContextMiddleware:
    """Pure-ASGI middleware for request-id propagation and access logging."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(_REQUEST_ID_HEADER.encode())
        request_id = incoming.decode() if incoming else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)

        method = scope.get("method", "-")
        path = scope.get("path", "-")
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw_headers = message.setdefault("headers", [])
                raw_headers.append(
                    (_REQUEST_ID_HEADER.encode(), request_id.encode())
                )
            await send(message)

        logger.info("request.start", extra={"method": method, "path": path})
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request.error",
                extra={"method": method, "path": path, "duration_ms": round(duration_ms, 2)},
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "request.end",
                extra={
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
        finally:
            request_id_ctx.reset(token)
