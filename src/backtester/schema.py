"""Compositional strategy rule schema.

A StrategyRule is data, not code: a boolean tree of comparisons over indicator
values and OHLCV references, evaluated bar by bar by the rule interpreter
(strategies/rule_strategy.py, not yet built). This module defines the schema and
validates it. It does not evaluate anything — that is evaluator.py's job.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from .indicators import CORE_INDICATORS, MAX_LOOKBACK


def _validate_offset(offset: int) -> int:
    if offset > 0:
        raise ValueError(f"offset must be <= 0 (positive offset is lookahead), got {offset}")
    if offset < -MAX_LOOKBACK:
        raise ValueError(f"offset must be >= -{MAX_LOOKBACK}, got {offset}")
    return offset


def _apply_cross_check(cross_check: dict, params: dict[str, float], indicator_name: str) -> None:
    """Enforce a registry-declared cross-param constraint, e.g. MACD fast < slow.

    Only checked when the rule explicitly supplies both referenced params — if
    either is left to the underlying pandas-ta default, there is nothing to check.
    """
    left_key, right_key = cross_check["left"], cross_check["right"]
    if left_key not in params or right_key not in params:
        return
    left_val, right_val = params[left_key], params[right_key]
    check_type = cross_check["type"]
    if check_type == "less_than" and not (left_val < right_val):
        raise ValueError(f"{indicator_name}: {left_key}={left_val} must be < {right_key}={right_val}")
    if check_type == "greater_than" and not (left_val > right_val):
        raise ValueError(f"{indicator_name}: {left_key}={left_val} must be > {right_key}={right_val}")
    if check_type == "not_equal" and left_val == right_val:
        raise ValueError(f"{indicator_name}: {left_key} must not equal {right_key}")


# ---------------------------------------------------------------------------
# Terms — anything that resolves to one number at a given bar
# ---------------------------------------------------------------------------

class _OffsetTerm(BaseModel):
    offset: int = 0

    @field_validator("offset")
    @classmethod
    def _check_offset(cls, v: int) -> int:
        return _validate_offset(v)


class BodyTerm(_OffsetTerm):
    """abs(open - close) — candle body size."""
    kind: Literal["body"] = "body"


class MidpointTerm(_OffsetTerm):
    """(open + close) / 2 — candle body midpoint."""
    kind: Literal["midpoint"] = "midpoint"


class RangeTerm(_OffsetTerm):
    """high - low — full bar range."""
    kind: Literal["range"] = "range"


class PriceTerm(_OffsetTerm):
    kind: Literal["price"] = "price"
    field: Literal["open", "high", "low", "close", "volume"]


class ConstantTerm(BaseModel):
    kind: Literal["constant"] = "constant"
    value: float


class IndicatorTerm(_OffsetTerm):
    kind: Literal["indicator"] = "indicator"
    name: str
    params: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_indicator(self) -> "IndicatorTerm":
        spec = CORE_INDICATORS.get(self.name)
        if spec is None:
            raise ValueError(f"unknown indicator {self.name!r}")
        if not spec.verified:
            raise ValueError(f"indicator {self.name!r} is not verified")
        for pname, pval in self.params.items():
            if pname not in spec.params:
                raise ValueError(f"{self.name} has no param {pname!r}")
            lo, hi = spec.params[pname]
            if not (lo <= pval <= hi):
                raise ValueError(f"{self.name}.{pname}={pval} out of bounds [{lo}, {hi}]")
        if spec.cross_check:
            _apply_cross_check(spec.cross_check, self.params, self.name)
        return self


class ScaledTerm(BaseModel):
    """Any other term multiplied by a positive constant, e.g. half of today's range."""
    kind: Literal["scaled"] = "scaled"
    term: Term
    factor: float

    @field_validator("factor")
    @classmethod
    def _check_factor(cls, v: float) -> float:
        if not (math.isfinite(v) and v > 0):
            raise ValueError(f"factor must be positive and finite, got {v}")
        return v

    @field_validator("term")
    @classmethod
    def _check_not_nested(cls, v: "Term") -> "Term":
        if isinstance(v, ScaledTerm):
            raise ValueError("ScaledTerm cannot wrap another ScaledTerm (no nesting)")
        return v


Term = Annotated[
    Union[IndicatorTerm, PriceTerm, ConstantTerm, BodyTerm, MidpointTerm, RangeTerm, ScaledTerm],
    Field(discriminator="kind"),
]

ScaledTerm.model_rebuild()


# ---------------------------------------------------------------------------
# Comparison and Condition — the boolean rule tree
# ---------------------------------------------------------------------------

class Comparison(BaseModel):
    left: Term
    op: Literal["gt", "lt", "gte", "lte", "crosses_above", "crosses_below", "eq_within"]
    right: Term
    tolerance: float | None = None

    @model_validator(mode="after")
    def _check_tolerance(self) -> "Comparison":
        if self.op == "eq_within" and self.tolerance is None:
            raise ValueError("eq_within requires tolerance to be set")
        if self.op != "eq_within" and self.tolerance is not None:
            raise ValueError(f"tolerance is only valid for eq_within, not {self.op!r}")
        return self


class Condition(BaseModel):
    kind: Literal["and", "or", "leaf"]
    comparison: Comparison | None = None
    children: list["Condition"] | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "Condition":
        if self.kind == "leaf":
            if self.comparison is None:
                raise ValueError("leaf condition requires a comparison")
            if self.children:
                raise ValueError("leaf condition must not have children")
        else:
            if self.comparison is not None:
                raise ValueError(f"{self.kind} condition must not have a comparison")
            if not self.children or len(self.children) < 2:
                raise ValueError(f"{self.kind} condition requires at least 2 children")
        return self


Condition.model_rebuild()


class StrategyRule(BaseModel):
    name: str
    description: str
    literature_source: str | None = None
    entry: Condition
    exit: Condition | None = None
    exit_after_bars: int | None = None

    @model_validator(mode="after")
    def _check_exit(self) -> "StrategyRule":
        if self.exit is None and self.exit_after_bars is None:
            raise ValueError("StrategyRule requires exit and/or exit_after_bars")
        if self.exit_after_bars is not None and self.exit_after_bars <= 0:
            raise ValueError("exit_after_bars must be positive")
        return self


# ---------------------------------------------------------------------------
# KNOWN_STRATEGIES — worked examples proving the schema is expressive enough
# ---------------------------------------------------------------------------

def _leaf(left: Term, op: str, right: Term, tolerance: float | None = None) -> Condition:
    return Condition(kind="leaf", comparison=Comparison(left=left, op=op, right=right, tolerance=tolerance))


SMA_CROSSOVER = StrategyRule(
    name="sma_10_30_crossover",
    description="Buy when the 10-day SMA crosses above the 30-day SMA; sell on the reverse crossover.",
    literature_source="Brock, Lakonishok & LeBaron (1992), Journal of Finance",
    entry=_leaf(
        IndicatorTerm(name="SMA", params={"length": 10}),
        "crosses_above",
        IndicatorTerm(name="SMA", params={"length": 30}),
    ),
    exit=_leaf(
        IndicatorTerm(name="SMA", params={"length": 10}),
        "crosses_below",
        IndicatorTerm(name="SMA", params={"length": 30}),
    ),
)

RSI_14_30_70 = StrategyRule(
    name="rsi_14_30_70",
    description="Buy when RSI(14) crosses below 30 (oversold); sell when it crosses above 70 (overbought).",
    literature_source="Wilder (1978), New Concepts in Technical Trading Systems",
    entry=_leaf(
        IndicatorTerm(name="RSI", params={"length": 14}), "crosses_below", ConstantTerm(value=30),
    ),
    exit=_leaf(
        IndicatorTerm(name="RSI", params={"length": 14}), "crosses_above", ConstantTerm(value=70),
    ),
)

RSI_2_10_90 = StrategyRule(
    name="rsi_2_10_90",
    description="Short-horizon mean reversion: buy when RSI(2) crosses below 10; sell when it crosses above 90.",
    literature_source="Connors & Alvarez (2009), Short Term Trading Strategies That Work",
    entry=_leaf(
        IndicatorTerm(name="RSI", params={"length": 2}), "crosses_below", ConstantTerm(value=10),
    ),
    exit=_leaf(
        IndicatorTerm(name="RSI", params={"length": 2}), "crosses_above", ConstantTerm(value=90),
    ),
)

MORNING_STAR = StrategyRule(
    name="morning_star",
    description=(
        "Three-bar bullish reversal candlestick pattern: a long bearish candle, a "
        "small-bodied 'star' that gaps down, then a long bullish candle closing back "
        "above the midpoint of the first candle's body."
    ),
    literature_source="Nison (1991), Japanese Candlestick Charting Techniques",
    entry=Condition(
        kind="and",
        children=[
            # Bar -2: long bearish candle
            _leaf(PriceTerm(field="close", offset=-2), "lt", PriceTerm(field="open", offset=-2)),
            _leaf(BodyTerm(offset=-2), "gt", ScaledTerm(term=RangeTerm(offset=-2), factor=0.6)),
            # Bar -1: small-bodied star, gapping down from bar -2's close
            _leaf(BodyTerm(offset=-1), "lt", ScaledTerm(term=RangeTerm(offset=-1), factor=0.3)),
            _leaf(PriceTerm(field="close", offset=-1), "lt", PriceTerm(field="close", offset=-2)),
            # Bar 0: long bullish candle recovering into bar -2's body
            _leaf(PriceTerm(field="close", offset=0), "gt", PriceTerm(field="open", offset=0)),
            _leaf(BodyTerm(offset=0), "gt", ScaledTerm(term=RangeTerm(offset=0), factor=0.6)),
            _leaf(PriceTerm(field="close", offset=0), "gt", MidpointTerm(offset=-2)),
        ],
    ),
    exit_after_bars=5,
)

KNOWN_STRATEGIES: dict[str, StrategyRule] = {
    "sma_10_30_crossover": SMA_CROSSOVER,
    "rsi_14_30_70": RSI_14_30_70,
    "rsi_2_10_90": RSI_2_10_90,
    "morning_star": MORNING_STAR,
}
