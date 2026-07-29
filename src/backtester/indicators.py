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
from typing import Callable, Literal

import pandas_ta as ta

# Positive offsets are a lookahead violation (Sacred Gate 1, extended to the schema
# layer). Negative offsets look back at most this many bars.
MAX_LOOKBACK = 5

_PRICE_FIELDS = ("open", "high", "low", "close", "volume")


def _infer_inputs(fn: Callable) -> tuple[str, ...]:
    """Which price columns `fn` requires, read off its signature.

    A parameter counts as required input only if its name is one of the five OHLCV
    fields AND it has no default — e.g. `ad`'s `open_` parameter defaults to None,
    so it is optional and excluded, leaving `ad` on (high, low, close, volume).
    This is provably correct because it reads what the function declares; nothing
    here is guessed or hardcoded from memory of the library's behaviour.
    """
    sig = inspect.signature(fn)
    return tuple(
        name
        for name, param in sig.parameters.items()
        if name in _PRICE_FIELDS and param.default is inspect.Parameter.empty
    )


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
