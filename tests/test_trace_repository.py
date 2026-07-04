"""Unit tests for :class:`TraceRepository` (SQLite persistence)."""

from __future__ import annotations

import json
import os

import aiosqlite

from app.repositories.trace_repository import TraceRepository


async def _rows(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM traces ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def test_initialize_creates_database_file(tmp_db_path: str) -> None:
    assert not os.path.exists(tmp_db_path)
    repo = TraceRepository(tmp_db_path)
    await repo.initialize()
    assert os.path.exists(tmp_db_path)
    await repo.close()


async def test_initialize_creates_nested_directories(tmp_path) -> None:
    nested = str(tmp_path / "a" / "b" / "traces.db")
    repo = TraceRepository(nested)
    await repo.initialize()
    assert os.path.exists(nested)
    await repo.close()


async def test_save_trace_persists_row(tmp_db_path: str) -> None:
    repo = TraceRepository(tmp_db_path)
    await repo.initialize()

    await repo.save_trace(
        request_id="req-42",
        primary_response='{"action": "buy"}',
        candidate_response='{"action": "sell"}',
        evaluation_result={
            "json_valid": True,
            "primary_action": "buy",
            "candidate_action": "sell",
            "exact_match": False,
        },
    )
    await repo.close()

    rows = await _rows(tmp_db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-42"
    assert json.loads(row["primary_response"]) == {"action": "buy"}
    assert json.loads(row["candidate_response"]) == {"action": "sell"}
    assert json.loads(row["evaluation_result"])["exact_match"] is False
    assert row["timestamp"]  # ISO timestamp recorded


async def test_save_trace_serializes_non_string_bodies(tmp_db_path: str) -> None:
    repo = TraceRepository(tmp_db_path)
    await repo.initialize()
    await repo.save_trace(
        request_id="req-obj",
        primary_response={"action": "buy"},
        candidate_response={"action": "hold"},
        evaluation_result={"exact_match": False},
    )
    await repo.close()

    rows = await _rows(tmp_db_path)
    assert json.loads(rows[0]["primary_response"]) == {"action": "buy"}


async def test_save_trace_without_init_never_raises(tmp_db_path: str) -> None:
    repo = TraceRepository(tmp_db_path)  # not initialized
    # Must not raise even though the connection is absent.
    await repo.save_trace(
        request_id="x",
        primary_response="{}",
        candidate_response="{}",
        evaluation_result={},
    )
    assert not os.path.exists(tmp_db_path)


async def test_close_is_safe_to_call_when_not_open(tmp_db_path: str) -> None:
    repo = TraceRepository(tmp_db_path)
    await repo.close()  # no-op, must not raise
