"""Two-tier indicator registry.

This module is a catalogue, not a computation layer: each ``IndicatorSpec`` describes
how to call a pandas-ta function and how to read its output. It does not run
indicators itself — the rule interpreter (``strategies/rule_strategy.py``) does that,
using this registry to know what is legal and how to wire it up.

Two tiers:
- ``core``: ~28 entries below, hand-picked, hand-verified against pandas-ta 0.4.71b0.
- ``extended``: auto-generated from the rest of the pandas-ta catalogue (see
  ``scripts/generate_extended_indicators.py`` and ``verify_extended_indicators.py``).
  Entries start ``verified=False`` and are only usable once verification flips them.
"""

import inspect
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal, get_args

import numpy as np
import pandas as pd
import pandas_ta as ta

# Positive offsets are a lookahead violation (Sacred Gate 1, extended to the schema
# layer). Negative offsets look back at most this many bars.
MAX_LOOKBACK = 5


def validate_offset(offset: int) -> int:
    """Shared bound check: -MAX_LOOKBACK <= offset <= 0, positive = lookahead.

    Used by both schema.py (construction-time) and evaluator.py (evaluation-time,
    including offsets evaluator.py derives itself, e.g. crossover's "previous bar",
    which schema.py never sees and so cannot validate).
    """
    if offset > 0:
        raise ValueError(f"offset must be <= 0 (positive offset is lookahead), got {offset}")
    if offset < -MAX_LOOKBACK:
        raise ValueError(f"offset must be >= -{MAX_LOOKBACK}, got {offset}")
    return offset


_PRICE_FIELDS = ("open", "high", "low", "close", "volume")

# pandas-ta names its "open" parameter `open_`, dodging the Python builtin. Every
# other OHLCV field keeps its plain name. This maps the raw signature name back to
# the canonical field name used everywhere else in this module (PriceTerm.field,
# _FIELD_TO_ATTR in rule_strategy.py, ...). Confirmed empirically: without this
# alias, _infer_inputs silently reports zero required inputs for every function
# that needs open price (ha, cdl_pattern, bop, brar, qstick, and others), and the
# function then blows up with "missing required positional argument" the moment
# it's actually called -- a real, previously-undetected gap, invisible until the
# extended-indicator sweep exercised a function that needed open price.
_PARAM_ALIASES = {"open_": "open"}


def _infer_inputs(fn: Callable) -> tuple[str, ...]:
    """Which price columns `fn` requires, read off its signature.

    A parameter counts as required input only if its (aliased) name is one of the
    five OHLCV fields AND it has no default — e.g. `ad`'s `open_` parameter defaults
    to None, so it is optional and excluded, leaving `ad` on (high, low, close,
    volume). This is provably correct because it reads what the function declares;
    nothing here is guessed or hardcoded from memory of the library's behaviour.
    Order matches signature order, which is what callers rely on when passing
    price series positionally.
    """
    sig = inspect.signature(fn)
    result = []
    for name, param in sig.parameters.items():
        canonical = _PARAM_ALIASES.get(name, name)
        if canonical in _PRICE_FIELDS and param.default is inspect.Parameter.empty:
            result.append(canonical)
    return tuple(result)


# Cross-cutting pandas-ta kwargs that are never exposed as tunable rule parameters.
# `offset` is excluded for a safety reason, not just tidiness: it shifts pandas-ta's
# entire OUTPUT SERIES forward or backward. Exposing it as an ordinary tunable
# parameter would let a StrategyRule silently request a shifted series under the
# guise of an indicator setting -- a lookahead vector one level removed from where
# Sacred Gate 1 actually checks. Left at pandas-ta's own default (unset) instead.
_NON_TUNABLE_PARAMS = {"append", "offset", "fillna", "fill_method", "signal_indicators", "col_names"}
_NUMERIC_TYPES = (int, float, np.integer, np.floating)


def _is_numeric_annotation(annotation: object) -> bool:
    """True if `annotation` is (or unions in) a numeric type, and is not str/bool.

    pandas-ta types every tunable parameter as e.g. `Union[int, numpy.integer,
    float, numpy.floating] = None` -- the DEFAULT is always None (the real numeric
    default lives inside the function body, invisible to `inspect`), so "is this a
    tunable numeric param" cannot be read off the default's type (verified: doing
    so found zero numeric params across 129 candidate functions). It has to be read
    off the annotation. `bool` is excluded explicitly before the numeric check
    because `isinstance(True, int)` is True in Python -- a categorical flag like
    `talib: bool` must never be mistaken for a numeric bound.
    """
    if annotation is inspect.Parameter.empty:
        return False
    args = get_args(annotation) or (annotation,)
    if any(a in (str, bool) for a in args):
        return False
    return any(a in _NUMERIC_TYPES for a in args)


def _numeric_tunable_params(fn: Callable) -> tuple[str, ...]:
    """Parameter names with a numeric type annotation, excluding OHLCV inputs and
    pandas-ta's own cross-cutting kwargs (see _NON_TUNABLE_PARAMS).

    This is the one piece of "does code determine param existence" that isn't
    provable from a default value alone (see _is_numeric_annotation) -- it is still
    fully deterministic, just keyed off the annotation instead. The LLM's only job,
    downstream of this, is proposing (min, max) bounds for exactly the names this
    function returns -- never which names are tunable in the first place.
    """
    sig = inspect.signature(fn)
    out = []
    for name, param in sig.parameters.items():
        canonical = _PARAM_ALIASES.get(name, name)
        if canonical in _PRICE_FIELDS:
            continue
        if name in _NON_TUNABLE_PARAMS or name == "kwargs":
            continue
        if _is_numeric_annotation(param.annotation):
            out.append(name)
    return tuple(out)


def select_output_column(result: "pd.DataFrame | pd.Series", column_prefix: str | None) -> "pd.Series":
    """Given a pandas-ta function's raw return value and an optional column_prefix,
    return the single Series a caller should actually read.

    Raises ValueError if column_prefix is set but doesn't match exactly 1 column.
    Shared by strategies/rule_strategy.py (enforced every bar at runtime) and
    scripts/verify_extended_indicators.py (enforced once at verification time) so
    both agree on what "the prefix matched" means -- this was inline, duplicated
    logic in rule_strategy.py before the extended-indicator verify script needed
    the identical check.
    """
    if column_prefix is None:
        return result
    cols = [c for c in result.columns if c.startswith(column_prefix)]
    if len(cols) != 1:
        raise ValueError(f"column_prefix {column_prefix!r} matched {len(cols)} columns, expected exactly 1")
    return result[cols[0]]


def normalize_params(params: dict[str, float]) -> dict[str, float | int]:
    """Whole-valued floats become int; e.g. length=10.0 -> 10.

    Registry params are uniformly typed dict[str, float] for consistent bounds-
    checking in schema.py, but some pandas-ta indicators (e.g. sma's numba-jitted
    path) require a genuine Python int for bar-count params and raise (a numba
    TypingError, or plain TypeError/ValueError depending on the function) on a
    float. Confirmed this isn't cosmetic: calling extended-indicator candidates at
    their proposed float bounds during generation failed this exact way across
    dozens of functions before this normalization was applied here.

    Safe for genuinely fractional params too (e.g. bbands' lower_std=1.0 vs 1):
    a whole-valued float and the equivalent int produce numerically identical
    output, differing only in cosmetic column-name formatting ("1.0" vs "1"),
    which column_prefix matching (prefix-only) is already indifferent to.

    Shared by strategies/rule_strategy.py (the runtime path) and both
    scripts/generate_extended_indicators.py and scripts/verify_extended_indicators.py
    (the build-time paths) -- one function, so all three ways of calling a
    pandas-ta function with registry params agree on what "the params" means.
    """
    return {k: (int(v) if float(v).is_integer() else v) for k, v in params.items()}


def _common_prefix(a: str, b: str) -> str:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[:i]


def _derive_column_prefixes(*results: "pd.DataFrame | pd.Series") -> list[str] | None:
    """One column_prefix per output column, matched by position -- without knowing
    anything about what a column means.

    Deterministic, not guessed or LLM-proposed: execute the function at 2+
    different parameter settings and fold the longest common prefix of each
    output column's name across all of them, matched by position. Validated
    against real multi-output pandas-ta functions (aroon, cksp, accbands):
    reproduces exactly the hand-picked core registry's own naming style
    (BBL_/BBM_/BBU_) with zero manual naming.

    Two sample points are not always enough: confirmed for real that kc's
    `length` bounds (1, 100) sample as column names "KCBe_1..." and "KCBe_100...",
    which coincidentally share a leading "1" digit -- a naive 2-point diff derives
    "KCBe_1" as if it were stable literal text, when the "1" is actually still
    part of the varying length parameter (and goes stale the moment a rule, or
    this registry's own sensitivity check, calls the function with any other
    length). A third sample point (e.g. the bounds' midpoint) breaks that
    coincidence in the overwhelming majority of cases, since three genuinely
    different parameter values sharing the same leading digits by chance is rare.

    Returns None for a Series result (no column, no prefix needed -- matches
    core's SMA/RSI/etc., which never set column_prefix). Pass a single result for
    a function with no tunable numeric params (nothing to sample twice): the
    common prefix of one string with itself is the string itself, which is
    exactly correct -- the exact column name, trivially matching only itself.
    """
    if not results:
        raise ValueError("_derive_column_prefixes requires at least one result")
    first = results[0]
    if not isinstance(first, pd.DataFrame):
        return None
    prefixes = list(first.columns)
    for other in results[1:]:
        other_cols = list(other.columns)
        if len(other_cols) != len(prefixes):
            raise ValueError(f"column count changed between calls: {prefixes} vs {other_cols}")
        prefixes = [_common_prefix(p, c) for p, c in zip(prefixes, other_cols)]
    return prefixes


@dataclass(frozen=True)
class IndicatorSpec:
    fn: Callable
    inputs: tuple[str, ...]
    params: dict[str, tuple[float, float]] = field(default_factory=dict)
    column_prefix: str | None = None
    cross_check: dict | None = None
    tier: Literal["core", "extended"] = "core"
    verified: bool = True
    verified_on: date | None = None
    lib_version: str | None = None


def _core(
    fn: Callable,
    params: dict[str, tuple[float, float]] | None = None,
    column_prefix: str | None = None,
    cross_check: dict | None = None,
) -> IndicatorSpec:
    """Build a core-tier entry. Inputs are inferred, never typed by hand."""
    return IndicatorSpec(
        fn=fn,
        inputs=_infer_inputs(fn),
        params=params or {},
        column_prefix=column_prefix,
        cross_check=cross_check,
        tier="core",
        verified=True,
    )


def _extended(
    fn: Callable,
    params: dict[str, tuple[float, float]] | None = None,
    column_prefix: str | None = None,
    cross_check: dict | None = None,
) -> IndicatorSpec:
    """Build a candidate extended-tier entry -- same shape as _core(), but
    verified=False until scripts/verify_extended_indicators.py confirms it
    empirically (execution, per-param sensitivity, cross-check, non-NaN checks).
    Used by scripts/generate_extended_indicators.py, which is the only caller that
    should ever construct one of these with verified=False params it hasn't run
    itself -- everything else in this codebase only ever sees the result after
    verification has had a chance to flip it.
    """
    return IndicatorSpec(
        fn=fn,
        inputs=_infer_inputs(fn),
        params=params or {},
        column_prefix=column_prefix,
        cross_check=cross_check,
        tier="extended",
        verified=False,
    )


CORE_INDICATORS: dict[str, IndicatorSpec] = {
    # --- Trend / moving averages (Series, close-only) ---
    "SMA": _core(ta.sma, params={"length": (2, 200)}),
    "EMA": _core(ta.ema, params={"length": (2, 200)}),
    "WMA": _core(ta.wma, params={"length": (2, 200)}),
    "DEMA": _core(ta.dema, params={"length": (2, 200)}),
    "TEMA": _core(ta.tema, params={"length": (2, 200)}),
    "HMA": _core(ta.hma, params={"length": (2, 200)}),

    # --- Momentum ---
    "RSI": _core(ta.rsi, params={"length": (2, 100)}),
    "CCI": _core(ta.cci, params={"length": (2, 100)}),
    "ROC": _core(ta.roc, params={"length": (1, 100)}),
    "MOM": _core(ta.mom, params={"length": (1, 100)}),
    "WILLR": _core(ta.willr, params={"length": (2, 100)}),
    "MACD": _core(
        ta.macd,
        params={"fast": (2, 50), "slow": (2, 200), "signal": (2, 50)},
        column_prefix="MACD_",
        cross_check={"type": "less_than", "left": "fast", "right": "slow"},
    ),
    "MACD_SIGNAL": _core(
        ta.macd,
        params={"fast": (2, 50), "slow": (2, 200), "signal": (2, 50)},
        column_prefix="MACDs_",
        cross_check={"type": "less_than", "left": "fast", "right": "slow"},
    ),
    "MACD_HISTOGRAM": _core(
        ta.macd,
        params={"fast": (2, 50), "slow": (2, 200), "signal": (2, 50)},
        column_prefix="MACDh_",
        cross_check={"type": "less_than", "left": "fast", "right": "slow"},
    ),
    "STOCH_K": _core(
        ta.stoch,
        # d=1 raises inside pandas-ta 0.4.71b0 regardless of k/smooth_k — verified.
        params={"k": (1, 50), "d": (2, 50), "smooth_k": (1, 50)},
        column_prefix="STOCHk_",
    ),
    "STOCH_D": _core(
        ta.stoch,
        params={"k": (1, 50), "d": (2, 50), "smooth_k": (1, 50)},
        column_prefix="STOCHd_",
    ),

    # --- Volatility ---
    "ATR": _core(ta.atr, params={"length": (2, 100)}),
    "NATR": _core(ta.natr, params={"length": (2, 100)}),
    "TRUE_RANGE": _core(ta.true_range),  # no tunable params in this version
    "BB_LOWER": _core(
        ta.bbands,
        params={"length": (2, 100), "lower_std": (0.5, 4.0), "upper_std": (0.5, 4.0)},
        column_prefix="BBL_",
    ),
    "BB_MID": _core(
        ta.bbands,
        params={"length": (2, 100), "lower_std": (0.5, 4.0), "upper_std": (0.5, 4.0)},
        column_prefix="BBM_",
    ),
    "BB_UPPER": _core(
        ta.bbands,
        params={"length": (2, 100), "lower_std": (0.5, 4.0), "upper_std": (0.5, 4.0)},
        column_prefix="BBU_",
    ),

    # --- Volume ---
    "OBV": _core(ta.obv),
    "VWAP": _core(ta.vwap),  # anchor/bands are categorical, not exposed as bounds
    "MFI": _core(ta.mfi, params={"length": (2, 100)}),
    "AD": _core(ta.ad),

    # --- Trend strength ---
    "ADX": _core(ta.adx, params={"length": (2, 100)}, column_prefix="ADX_"),
    "DMP": _core(ta.adx, params={"length": (2, 100)}, column_prefix="DMP_"),
    "DMN": _core(ta.adx, params={"length": (2, 100)}, column_prefix="DMN_"),
}
