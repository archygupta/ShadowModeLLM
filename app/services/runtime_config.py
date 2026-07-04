"""Mutable runtime configuration.

Unlike :class:`app.config.Settings` (loaded once from the environment), this
holds values that can change at runtime via the API - currently the shadow
routing percentage. A single instance is shared for the process lifetime and
its writes are serialized with an :class:`asyncio.Lock`.

Reads of a single ``int`` are atomic in CPython, so :meth:`should_mirror` and
the ``shadow_percentage`` property don't take the lock; only mutation does.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any


class RuntimeConfig:
    """Process-wide, runtime-adjustable configuration."""

    def __init__(self, shadow_percentage: int) -> None:
        self._shadow_percentage = shadow_percentage
        self._lock = asyncio.Lock()

    @property
    def shadow_percentage(self) -> int:
        return self._shadow_percentage

    async def set_shadow_percentage(self, value: int) -> int:
        """Atomically update the shadow percentage; returns the new value.

        Callers are expected to have validated ``0 <= value <= 100`` already
        (the API schema enforces this), but we clamp defensively.
        """

        clamped = max(0, min(100, value))
        async with self._lock:
            self._shadow_percentage = clamped
            return self._shadow_percentage

    def should_mirror(self) -> bool:
        """Return whether the current request should be mirrored to the candidate.

        Uses the live percentage, so changes take effect on the very next
        request without a restart.
        """

        pct = self._shadow_percentage
        if pct >= 100:
            return True
        if pct <= 0:
            return False
        return random.uniform(0, 100) < pct

    def snapshot(self) -> dict[str, Any]:
        """Return the current runtime configuration."""

        return {"shadow_percentage": self._shadow_percentage}
