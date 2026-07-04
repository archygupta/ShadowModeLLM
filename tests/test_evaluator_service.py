"""Unit tests for :class:`EvaluatorService` (JSON validity + action match)."""

from __future__ import annotations

import pytest

from app.services.evaluator_service import EvaluatorService


@pytest.fixture
def evaluator() -> EvaluatorService:
    return EvaluatorService()


def test_matching_action_exact(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate('{"action": "buy"}', '{"action": "buy"}')
    assert result.json_valid is True
    assert result.primary_action == "buy"
    assert result.candidate_action == "buy"
    assert result.exact_match is True


def test_mismatching_action(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate('{"action": "buy"}', '{"action": "sell"}')
    assert result.json_valid is True
    assert result.primary_action == "buy"
    assert result.candidate_action == "sell"
    assert result.exact_match is False


def test_action_comparison_is_case_sensitive(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate('{"action": "Buy"}', '{"action": "buy"}')
    assert result.json_valid is True
    assert result.exact_match is False


def test_missing_action_becomes_none(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate('{"foo": 1}', '{"bar": 2}')
    assert result.json_valid is True
    assert result.primary_action is None
    assert result.candidate_action is None
    # Both None => equal => exact_match True (both "missing action").
    assert result.exact_match is True


def test_one_side_missing_action(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate('{"action": "buy"}', '{"other": true}')
    assert result.json_valid is True
    assert result.primary_action == "buy"
    assert result.candidate_action is None
    assert result.exact_match is False


def test_malformed_primary_json(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate("not-json", '{"action": "buy"}')
    assert result.json_valid is False
    assert result.primary_action is None
    assert result.candidate_action == "buy"
    assert result.exact_match is False


def test_malformed_both_json(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate("<<bad>>", "also-bad")
    assert result.json_valid is False
    assert result.exact_match is False


def test_accepts_predecoded_objects(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate({"action": "hold"}, {"action": "hold"})
    assert result.json_valid is True
    assert result.exact_match is True


def test_none_input_is_invalid(evaluator: EvaluatorService) -> None:
    result = evaluator.evaluate(None, '{"action": "buy"}')
    assert result.json_valid is False
    assert result.exact_match is False


def test_non_object_json_has_no_action(evaluator: EvaluatorService) -> None:
    # Valid JSON but not an object => action is None, still json_valid.
    result = evaluator.evaluate("[1, 2, 3]", "42")
    assert result.json_valid is True
    assert result.primary_action is None
    assert result.candidate_action is None
    assert result.exact_match is True


def test_evaluator_never_raises_on_weird_input(evaluator: EvaluatorService) -> None:
    class Weird:
        pass

    result = evaluator.evaluate(Weird(), Weird())
    assert result.json_valid is False
    assert result.exact_match is False
