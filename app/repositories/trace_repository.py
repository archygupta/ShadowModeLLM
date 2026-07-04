"""SQLite-backed persistence for divergence traces.

Stores a trace whenever the primary and candidate actions disagree, so
divergences can be inspected offline. Uses :mod:`aiosqlite` for non-blocking
I/O. The database file and schema are created automatically on
:meth:`initialize` if they do not already exist.

This repository is only ever exercised from the detached shadow pipeline (off
the request path), and :meth:`save_trace` additionally swallows its own errors,
so persistence can never block or break request handling.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    primary_response TEXT,
    candidate_response TEXT,
    evaluation_result TEXT
);
"""

_INSERT = """
INSERT INTO traces
    (timestamp, request_id, primary_response, candidate_response, evaluation_result)
VALUES (?, ?, ?, ?, ?);
"""


class TraceRepository:
    """Persists divergence traces to a SQLite database via aiosqlite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        # SQLite has a single writer; serialize writes on the shared connection.
        self._lock = asyncio.Lock()

    @property
    def is_ready(self) -> bool:
        """Whether the connection is open (used by readiness)."""

        return self._db is not None

    async def initialize(self) -> None:
        """Open the connection and create the database/table if missing."""

        path = Path(self._db_path).expanduser()
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(path))
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()
        logger.info("trace_repository.initialized", extra={"db_path": str(path)})

    async def save_trace(
        self,
        *,
        request_id: str,
        primary_response: Any,
        candidate_response: Any,
        evaluation_result: dict[str, Any],
    ) -> None:
        """Persist a single divergence trace. Never raises.

        ``primary_response``/``candidate_response`` are stored as raw text;
        ``evaluation_result`` is serialized to a JSON string.
        """

        if self._db is None:  # pragma: no cover - indicates a startup wiring bug
            logger.warning(
                "trace.persist_skipped",
                extra={"request_id": request_id, "reason": "repository_not_initialized"},
            )
            return

        try:
            timestamp = _dt.datetime.now(_dt.timezone.utc).isoformat()
            evaluation_json = json.dumps(evaluation_result, default=str)
            async with self._lock:
                await self._db.execute(
                    _INSERT,
                    (
                        timestamp,
                        request_id,
                        _as_text(primary_response),
                        _as_text(candidate_response),
                        evaluation_json,
                    ),
                )
                await self._db.commit()
            logger.info("trace.persisted", extra={"request_id": request_id})
        except Exception:  # noqa: BLE001 - persistence is best-effort
            logger.exception("trace.persist_failed", extra={"request_id": request_id})

    async def close(self) -> None:
        """Close the underlying connection (used on shutdown)."""

        if self._db is not None:
            await self._db.close()
            self._db = None


def _as_text(value: Any) -> str | None:
    """Coerce a response body to text for storage."""

    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, default=str)
