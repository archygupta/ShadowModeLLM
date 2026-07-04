"""Structured error types for outbound LLM calls.

A single :class:`UpstreamLLMError` base carries the two things callers need: a
human-readable ``detail`` and the HTTP ``status_code`` the API layer should
surface. The primary and candidate services subclass it so their failure modes
share one construction path instead of duplicating it.
"""

from __future__ import annotations


class UpstreamLLMError(Exception):
    """Base class for structured upstream-LLM failures.

    Subclasses set sensible ``status_code``/``detail`` class defaults; both can
    still be overridden per-instance.
    """

    status_code: int = 502
    detail: str = "Upstream LLM request failed."

    def __init__(
        self, detail: str | None = None, *, status_code: int | None = None
    ) -> None:
        if detail is not None:
            self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.detail)
