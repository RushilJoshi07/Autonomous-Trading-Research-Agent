"""Tests for evaluator.py's pure term/condition evaluation.

Isolated from backtesting.py entirely, per evaluator.py's own module docstring:
everything here goes through a hand-built BarContext test double, not a real
backtest, so exact values (including NaN) at exact offsets are fully controlled.
"""

import math

import pytest

from backtester.evaluator import evaluate_comparison, evaluate_condition, resolve_term
from backtester.schema import (
    BodyTerm,
    Comparison,
    Condition,
    ConstantTerm,
    MidpointTerm,
    PriceTerm,
    RangeTerm,
    ScaledTerm,
)


class FakeBarContext:
    """BarContext backed by plain dicts, keyed (field, offset). Deliberately
    strict (KeyError on an unset lookup) so a test's setup is always explicit
    about exactly which values matter."""

    def __init__(self, prices: dict[tuple[str, int], float] | None = None):
        self._prices = prices or {}

    def price(self, field: str, offset: int) -> float:
        return self._prices[(field, offset)]

    def indicator(self, key, offset: int) -> float:
        raise NotImplementedError("not needed for these tests")


def _leaf(left, op, right, tolerance=None) -> Condition:
    return Condition(kind="leaf", comparison=Comparison(left=left, op=op, right=right, tolerance=tolerance))


# ---------------------------------------------------------------------------
# Comparison operators
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "op, close, constant, expected",
    [
        ("gt", 105.0, 100.0, True),
        ("gt", 95.0, 100.0, False),
        ("lt", 95.0, 100.0, True),
        ("lt", 105.0, 100.0, False),
        ("gte", 100.0, 100.0, True),
        ("gte", 99.0, 100.0, False),
        ("lte", 100.0, 100.0, True),
        ("lte", 101.0, 100.0, False),
    ],
)
def test_comparison_ops(op, close, constant, expected):
    ctx = FakeBarContext({("close", 0): close})
    cmp = Comparison(left=PriceTerm(field="close"), op=op, right=ConstantTerm(value=constant))
    assert evaluate_comparison(cmp, ctx) is expected


def test_eq_within_true_when_inside_tolerance():
    ctx = FakeBarContext({("close", 0): 100.4})
    cmp = Comparison(left=PriceTerm(field="close"), op="eq_within", right=ConstantTerm(value=100.0), tolerance=0.5)
    assert evaluate_comparison(cmp, ctx) is True


def test_eq_within_false_when_outside_tolerance():
    ctx = FakeBarContext({("close", 0): 100.6})
    cmp = Comparison(left=PriceTerm(field="close"), op="eq_within", right=ConstantTerm(value=100.0), tolerance=0.5)
    assert evaluate_comparison(cmp, ctx) is False


def test_nan_operand_makes_plain_comparison_false_not_raise():
    ctx = FakeBarContext({("close", 0): float("nan")})
    cmp = Comparison(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=100.0))
    assert evaluate_comparison(cmp, ctx) is False


# ---------------------------------------------------------------------------
# Crossover: true only on a real flip, false on any NaN among the 4 values
# ---------------------------------------------------------------------------

def test_crosses_above_true_on_real_flip():
    # previous bar: high(5) < low(10); current bar: high(15) > low(10) -- a real flip.
    ctx = FakeBarContext({("high", -1): 5.0, ("low", -1): 10.0, ("high", 0): 15.0, ("low", 0): 10.0})
    cmp = Comparison(left=PriceTerm(field="high"), op="crosses_above", right=PriceTerm(field="low"))
    assert evaluate_comparison(cmp, ctx) is True


def test_crosses_above_false_when_already_above_both_bars():
    # left is above right on BOTH bars -- no flip happened, so this must not fire.
    ctx = FakeBarContext({("high", -1): 20.0, ("low", -1): 10.0, ("high", 0): 25.0, ("low", 0): 10.0})
    cmp = Comparison(left=PriceTerm(field="high"), op="crosses_above", right=PriceTerm(field="low"))
    assert evaluate_comparison(cmp, ctx) is False


def test_crosses_below_true_on_real_flip():
    ctx = FakeBarContext({("high", -1): 15.0, ("low", -1): 10.0, ("high", 0): 5.0, ("low", 0): 10.0})
    cmp = Comparison(left=PriceTerm(field="high"), op="crosses_below", right=PriceTerm(field="low"))
    assert evaluate_comparison(cmp, ctx) is True


@pytest.mark.parametrize("nan_key", [("high", -1), ("low", -1), ("high", 0), ("low", 0)])
def test_crosses_above_false_when_any_of_four_values_is_nan(nan_key):
    values = {("high", -1): 5.0, ("low", -1): 10.0, ("high", 0): 15.0, ("low", 0): 10.0}
    values[nan_key] = float("nan")
    ctx = FakeBarContext(values)
    cmp = Comparison(left=PriceTerm(field="high"), op="crosses_above", right=PriceTerm(field="low"))
    assert evaluate_comparison(cmp, ctx) is False


# ---------------------------------------------------------------------------
# Condition composition: and / or, including 3-child cases
# ---------------------------------------------------------------------------

def test_and_condition_requires_all_children_true():
    ctx = FakeBarContext({("close", 0): 100.0})
    true_leaf = _leaf(PriceTerm(field="close"), "gt", ConstantTerm(value=50.0))
    false_leaf = _leaf(PriceTerm(field="close"), "lt", ConstantTerm(value=50.0))
    assert evaluate_condition(Condition(kind="and", children=[true_leaf, true_leaf]), ctx) is True
    assert evaluate_condition(Condition(kind="and", children=[true_leaf, false_leaf]), ctx) is False


def test_and_condition_three_children_one_false_fails_whole():
    ctx = FakeBarContext({("close", 0): 100.0})
    true_leaf = _leaf(PriceTerm(field="close"), "gt", ConstantTerm(value=50.0))
    false_leaf = _leaf(PriceTerm(field="close"), "lt", ConstantTerm(value=50.0))
    cond = Condition(kind="and", children=[true_leaf, true_leaf, false_leaf])
    assert evaluate_condition(cond, ctx) is False


def test_or_condition_three_children_one_true_passes_whole():
    ctx = FakeBarContext({("close", 0): 100.0})
    true_leaf = _leaf(PriceTerm(field="close"), "gt", ConstantTerm(value=50.0))
    false_leaf = _leaf(PriceTerm(field="close"), "lt", ConstantTerm(value=50.0))
    cond = Condition(kind="or", children=[false_leaf, false_leaf, true_leaf])
    assert evaluate_condition(cond, ctx) is True


def test_or_condition_all_false_children_fails():
    ctx = FakeBarContext({("close", 0): 100.0})
    false_leaf = _leaf(PriceTerm(field="close"), "lt", ConstantTerm(value=50.0))
    cond = Condition(kind="or", children=[false_leaf, false_leaf])
    assert evaluate_condition(cond, ctx) is False


# ---------------------------------------------------------------------------
# BodyTerm / MidpointTerm / RangeTerm / ScaledTerm
# ---------------------------------------------------------------------------

def test_body_term_is_abs_open_minus_close():
    ctx = FakeBarContext({("open", 0): 10.0, ("close", 0): 15.0})
    assert resolve_term(BodyTerm(offset=0), ctx) == 5.0


def test_midpoint_term_is_average_of_open_and_close():
    ctx = FakeBarContext({("open", 0): 10.0, ("close", 0): 16.0})
    assert resolve_term(MidpointTerm(offset=0), ctx) == 13.0


def test_range_term_is_high_minus_low():
    ctx = FakeBarContext({("high", 0): 20.0, ("low", 0): 8.0})
    assert resolve_term(RangeTerm(offset=0), ctx) == 12.0


def test_scaled_term_multiplies_inner_value_by_factor():
    ctx = FakeBarContext({("close", 0): 10.0})
    term = ScaledTerm(term=PriceTerm(field="close"), factor=3.0)
    assert resolve_term(term, ctx) == 30.0


# ---------------------------------------------------------------------------
# Sacred Gate 1 extension: positive offset raises at evaluation time too
# ---------------------------------------------------------------------------

def test_valid_negative_offset_does_not_raise():
    ctx = FakeBarContext({("close", -1): 100.0})
    term = PriceTerm(field="close", offset=-1)
    assert resolve_term(term, ctx) == 100.0


def test_positive_offset_via_post_construction_mutation_raises():
    """schema.py's own validator blocks constructing a term with a positive
    offset directly -- this proves the evaluator has its own, independent
    check, not just a re-test of schema.py's. Pydantic models here aren't
    frozen or validate_assignment, so mutating .offset after construction
    bypasses schema.py's validator entirely; resolve_term must still catch it.
    """
    term = PriceTerm(field="close", offset=-1)
    term.offset = 1  # bypasses schema.py's construction-time validator
    with pytest.raises(ValueError, match="lookahead"):
        resolve_term(term, FakeBarContext())
