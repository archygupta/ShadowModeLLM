"""Structured logging configuration.

Provides :func:`configure_logging`, which wires up the standard library logging
stack via ``dictConfig``. When ``LOG_JSON`` is enabled each record is emitted as
a single JSON line (ideal for log aggregators); otherwise a concise, colorless
human-readable format is used for local development.

A :data:`request_id_ctx` context variable carries a per-request correlation id
so it can be injected into every log line emitted while handling a request.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from contextvars import ContextVar
from logging.config import dictConfig
from typing import Any

from app.config import Settings

# Correlation id for the in-flight request; "-" when outside a request scope.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes present on every ``logging.LogRecord``; anything outside this set
# is treated as a caller-supplied "extra" and included in structured output.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "taskName"}


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as compact single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # Merge any structured ``extra=...`` fields supplied by the caller.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and key != "request_id":
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    """Configure the root logger according to ``settings``.

    Idempotent: calling it multiple times simply reapplies the configuration,
    which is convenient for tests and for the app factory.
    """

    formatter: dict[str, Any]
    if settings.log_json:
        formatter = {"()": f"{__name__}.JsonFormatter"}
    else:
        formatter = {
            "format": (
                "%(asctime)s | %(levelname)-8s | %(name)s | "
                "req=%(request_id)s | %(message)s"
            ),
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_id": {"()": f"{__name__}.RequestIdFilter"},
            },
            "formatters": {"default": formatter},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["request_id"],
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": settings.log_level,
                "handlers": ["console"],
            },
            "loggers": {
                # Let our root handler own formatting; keep access logs quiet-ish.
                "uvicorn": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
                "uvicorn.error": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
                "uvicorn.access": {"level": "WARNING", "handlers": ["console"], "propagate": False},
            },
        }
    )
