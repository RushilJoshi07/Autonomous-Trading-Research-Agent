"""Pure evaluation of strategy rules against a bar context.

Given a Term/Comparison/Condition (from schema.py) and something that can answer
"what was this price/indicator at this offset" (a BarContext), compute an actual
number or true/false. Nothing here touches backtesting.py or any specific data
source — that coupling lives in strategies/rule_strategy.py (not yet built).
"""

from __future__ import annotations

import math
from typing import Protocol

from .indicators import validate_offset
from .schema import (
    BodyTerm,
    Comparison,
    Condition,
    ConstantTerm,
    IndicatorTerm,
    MidpointTerm,
    PriceTerm,
    RangeTerm,
    ScaledTerm,
    Term,
)

IndicatorKey = tuple[str, frozenset[tuple[str, float]]]


class BarContext(Protocol):
    def price(self, field: str, offset: int) -> float: ...
    def indicator(self, key: IndicatorKey, offset: int) -> float: ...


def indicator_key(term: IndicatorTerm) -> IndicatorKey:
    """The dedup key an IndicatorTerm resolves to. Component 5 uses the same
    function to key its precomputed indicators, so both sides always agree."""
    return (term.name, frozenset(term.params.items()))


def resolve_term(term: Term, ctx: BarContext) -> float:
    match term:
        case ConstantTerm():
            return term.value
        case ScaledTerm():
            return resolve_term(term.term, ctx) * term.factor
        case PriceTerm():
            validate_offset(term.offset)
            return ctx.price(term.field, term.offset)
        case IndicatorTerm():
            validate_offset(term.offset)
            return ctx.indicator(indicator_key(term), term.offset)
        case BodyTerm():
            validate_offset(term.offset)
            return abs(ctx.price("open", term.offset) - ctx.price("close", term.offset))
        case MidpointTerm():
            validate_offset(term.offset)
            return (ctx.price("open", term.offset) + ctx.price("close", term.offset)) / 2
        case RangeTerm():
            validate_offset(term.offset)
            return ctx.price("high", term.offset) - ctx.price("low", term.offset)
        case _:
            raise TypeError(f"unknown term kind: {term!r}")


def _shifted(term: Term, delta: int) -> Term:
    """A copy of `term` looking `delta` bars further back than its own offset.

    Used for crossover's "previous bar" reading. Validated here specifically
    because this offset is derived at evaluation time — schema.py only ever
    validated the term's own declared offset, never one shifted beyond it.
    """
    if isinstance(term, ConstantTerm):
        return term
    if isinstance(term, ScaledTerm):
        return term.model_copy(update={"term": _shifted(term.term, delta)})
    new_offset = validate_offset(term.offset + delta)
    return term.model_copy(update={"offset": new_offset})


def evaluate_comparison(cmp: Comparison, ctx: BarContext) -> bool:
    if cmp.op in ("crosses_above", "crosses_below"):
        return _evaluate_crossover(cmp, ctx)

    left = resolve_term(cmp.left, ctx)
    right = resolve_term(cmp.right, ctx)
    if math.isnan(left) or math.isnan(right):
        return False

    match cmp.op:
        case "gt":
            return left > right
        case "lt":
            return left < right
        case "gte":
            return left >= right
        case "lte":
            return left <= right
        case "eq_within":
            assert cmp.tolerance is not None  # guaranteed by Comparison's own validator
            return abs(left - right) <= cmp.tolerance
        case _:
            raise TypeError(f"unhandled comparison op: {cmp.op!r}")


def _evaluate_crossover(cmp: Comparison, ctx: BarContext) -> bool:
    left_now = resolve_term(cmp.left, ctx)
    right_now = resolve_term(cmp.right, ctx)
    left_prev = resolve_term(_shifted(cmp.left, -1), ctx)
    right_prev = resolve_term(_shifted(cmp.right, -1), ctx)

    if any(math.isnan(v) for v in (left_now, right_now, left_prev, right_prev)):
        return False

    if cmp.op == "crosses_above":
        return left_prev < right_prev and left_now > right_now
    return left_prev > right_prev and left_now < right_now


def evaluate_condition(cond: Condition, ctx: BarContext) -> bool:
    if cond.kind == "leaf":
        assert cond.comparison is not None  # guaranteed by Condition's own validator
        return evaluate_comparison(cond.comparison, ctx)

    assert cond.children is not None  # guaranteed by Condition's own validator
    values = (evaluate_condition(child, ctx) for child in cond.children)
    return all(values) if cond.kind == "and" else any(values)
