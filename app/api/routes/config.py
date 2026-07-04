"""Runtime configuration endpoint.

Allows adjusting the shadow routing percentage at runtime. Updates take effect
immediately for subsequent requests, with no server restart.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeConfigDep
from app.schemas.config import ConfigResponse, ConfigUpdateRequest

router = APIRouter(tags=["config"])


@router.get("/config", response_model=ConfigResponse, summary="Get runtime config")
async def get_config(runtime_config: RuntimeConfigDep) -> ConfigResponse:
    """Return the current runtime configuration."""

    return ConfigResponse(**runtime_config.snapshot())


@router.put("/config", response_model=ConfigResponse, summary="Update runtime config")
async def update_config(
    body: ConfigUpdateRequest, runtime_config: RuntimeConfigDep
) -> ConfigResponse:
    """Update the shadow routing percentage and return the updated config.

    ``shadow_percentage`` is validated to ``0 <= value <= 100`` by the request
    schema. The new value affects subsequent requests immediately.
    """

    await runtime_config.set_shadow_percentage(body.shadow_percentage)
    return ConfigResponse(**runtime_config.snapshot())
