"""Synchronous primary proxy endpoint.

``POST /v1/chat`` accepts an arbitrary JSON body, forwards it unchanged to the
primary LLM, and returns the primary's response immediately. All logic lives in
the service layer; this handler only wires the request together and shapes the
HTTP response.

A detached shadow pipeline (candidate call + evaluation) is scheduled per
request and is never awaited, so the client's response depends solely on the
primary.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from app.api.deps import (
    MetricsServiceDep,
    PrimaryLLMServiceDep,
    RuntimeConfigDep,
    ShadowExecutorDep,
    ShadowPipelineDep,
)
from app.logging import request_id_ctx
from app.schemas.chat import ChatErrorResponse
from app.services.primary_llm_service import PrimaryLLMError

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post(
    "/chat",
    summary="Forward a chat request to the primary LLM",
    responses={
        502: {"model": ChatErrorResponse, "description": "Upstream unreachable"},
        504: {"model": ChatErrorResponse, "description": "Upstream timed out"},
    },
)
async def chat(
    service: PrimaryLLMServiceDep,
    shadow_pipeline: ShadowPipelineDep,
    shadow_executor: ShadowExecutorDep,
    runtime_config: RuntimeConfigDep,
    metrics: MetricsServiceDep,
    payload: Any = Body(..., description="Arbitrary JSON forwarded to the primary LLM."),
) -> JSONResponse:
    """Proxy the request body to the primary LLM and return its response.

    The primary model always executes. Based on the runtime ``shadow_percentage``
    (adjustable via ``PUT /config``), the request may also be mirrored to the
    candidate: the same payload is submitted to the bounded shadow executor (a
    fixed worker pool draining a size-capped queue). If the queue is full the
    shadow job is dropped immediately; submission is O(1) and never awaited, so
    the client's response depends solely on the primary and is never delayed by
    candidate failures/latency/timeouts. Once a worker runs the job and the
    candidate finishes, the evaluator compares it against the primary response,
    handed over via ``primary_future``.
    """

    request_id = request_id_ctx.get()
    await metrics.record_request()

    # Sample this request for shadow mirroring per the live routing percentage.
    # The primary path below runs regardless of this decision.
    primary_future: asyncio.Future[Any] | None = None
    if runtime_config.should_mirror():
        primary_future = asyncio.get_running_loop().create_future()
        await shadow_executor.submit(
            lambda: shadow_pipeline.run(
                payload, request_id=request_id, primary_future=primary_future
            ),
            request_id=request_id,
        )

    primary_raw: Any = None
    try:
        result = await service.chat(payload, request_id=request_id)
        primary_raw = result.raw
        await metrics.record_primary_success(result.latency_ms)
    except PrimaryLLMError:
        await metrics.record_primary_failure()
        raise
    finally:
        # Unblock the pipeline with the raw primary body when mirroring is on.
        if primary_future is not None and not primary_future.done():
            primary_future.set_result(primary_raw)

    # Note: X-Request-ID is added by RequestContextMiddleware, so it is not set
    # here to avoid a duplicated header.
    return JSONResponse(
        status_code=result.status_code,
        content=result.content,
        headers={
            "X-Primary-Latency-Ms": str(result.latency_ms),
            "X-Primary-Retries": str(result.retries),
        },
    )
