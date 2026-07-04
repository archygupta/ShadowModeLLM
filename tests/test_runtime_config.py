"""Unit tests for :class:`RuntimeConfig` (mutable shadow routing)."""

from __future__ import annotations

import app.services.runtime_config as rc_module
from app.services.runtime_config import RuntimeConfig


async def test_initial_value_and_snapshot() -> None:
    config = RuntimeConfig(75)
    assert config.shadow_percentage == 75
    assert config.snapshot() == {"shadow_percentage": 75}


async def test_set_updates_value() -> None:
    config = RuntimeConfig(100)
    returned = await config.set_shadow_percentage(25)
    assert returned == 25
    assert config.shadow_percentage == 25


async def test_set_clamps_out_of_range() -> None:
    config = RuntimeConfig(50)
    assert await config.set_shadow_percentage(150) == 100
    assert await config.set_shadow_percentage(-10) == 0


def test_should_mirror_at_100_always_true() -> None:
    config = RuntimeConfig(100)
    assert all(config.should_mirror() for _ in range(50))


def test_should_mirror_at_0_always_false() -> None:
    config = RuntimeConfig(0)
    assert not any(config.should_mirror() for _ in range(50))


def test_should_mirror_partial_uses_random(monkeypatch) -> None:
    config = RuntimeConfig(50)

    # Deterministically drive the sampling boundary.
    monkeypatch.setattr(rc_module.random, "uniform", lambda a, b: 10.0)
    assert config.should_mirror() is True  # 10 < 50

    monkeypatch.setattr(rc_module.random, "uniform", lambda a, b: 90.0)
    assert config.should_mirror() is False  # 90 < 50 is False
