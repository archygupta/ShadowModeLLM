"""Shadow-mode evaluation service.

Compares a primary response against a candidate response. The evaluator is
intentionally small and pure:

  1. Validate that both responses are valid JSON.
  2. Extract a top-level ``"action"`` field from each (``None`` when absent).
  3. Compare the two action values exactly and case-sensitively.

It **never raises**: malformed input, wrong types, and unexpected errors all
resolve to a well-formed :class:`~app.schemas.evaluation.EvaluationResult`.

Evaluation is observational only; scoring, metrics, and persistence are out of
scope for now, and results are not exposed through any API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.schemas.evaluation import EvaluationResult

logger = logging.getLogger(__name__)

_MISSING = object()


class EvaluatorService:
    """Compares primary vs. candidate responses on their ``action`` field."""

    def evaluate(
        self, primary_response: Any, candidate_response: Any
    ) -> EvaluationResult:
        """Compare two responses and return a structured result.

        Accepts either raw JSON text (``str``/``bytes``) or already-decoded JSON
        objects. Guarantees no exception escapes.
        """

        try:
            primary_ok, primary_action = self._extract_action(primary_response)
            candidate_ok, candidate_action = self._extract_action(candidate_response)

            json_valid = primary_ok and candidate_ok
            exact_match = json_valid and self._actions_equal(
                primary_action, candidate_action
            )

            return EvaluationResult(
                json_valid=json_valid,
                primary_action=primary_action if primary_ok else None,
                candidate_action=candidate_action if candidate_ok else None,
                exact_match=exact_match,
            )
        except Exception:  # noqa: BLE001 - evaluator must never throw
            logger.exception("evaluator.unexpected_error")
            return EvaluationResult(
                json_valid=False,
                primary_action=None,
                candidate_action=None,
                exact_match=False,
            )

    @staticmethod
    def _actions_equal(primary_action: Any, candidate_action: Any) -> bool:
        """Exact, case-sensitive equality of two action values."""

        # Python `==` on str is already case-sensitive; guard against comparing
        # incompatible types raising (it doesn't for ==, but be explicit).
        return primary_action == candidate_action

    def _extract_action(self, response: Any) -> tuple[bool, Any]:
        """Return ``(is_valid_json, action_value)`` for a single response.

        ``action_value`` is the top-level ``"action"`` field when the parsed JSON
        is an object, otherwise ``None``.
        """

        is_valid, parsed = self._parse_json(response)
        if not is_valid:
            return False, None

        action = None
        if isinstance(parsed, dict):
            value = parsed.get("action", _MISSING)
            action = None if value is _MISSING else value
        return True, action

    @staticmethod
    def _parse_json(response: Any) -> tuple[bool, Any]:
        """Normalize input to ``(is_valid_json, parsed_value)`` without raising."""

        if response is None:
            return False, None

        # Raw JSON payloads: attempt to decode.
        if isinstance(response, (str, bytes, bytearray)):
            try:
                return True, json.loads(response)
            except (ValueError, TypeError):
                return False, None

        # Already-decoded JSON values (dict/list/number/bool) are valid by origin.
        if isinstance(response, (dict, list, int, float, bool)):
            return True, response

        # Anything else is not representable as JSON we can reason about.
        return False, None
