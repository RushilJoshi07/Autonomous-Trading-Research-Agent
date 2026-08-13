"""Shared helpers for generate_extended_indicators.py and verify_extended_indicators.py.

Two things live here because both scripts need them to behave identically:
- make_synthetic_ohlcv: deterministic price data both scripts execute pandas-ta
  functions against.
- render_extended_indicators_module: the single serializer for
  src/backtester/extended_indicators.py. Generation writes the file once with this;
  verification rewrites the same file in place with this after flipping
  verified/verified_on/lib_version -- if the two scripts had separate renderers they
  could silently drift apart in how they encode a spec.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backtester.indicators import IndicatorSpec


def make_synthetic_ohlcv(n_bars: int = 1500, seed: int = 7) -> pd.DataFrame:
    """Deterministic OHLCV data for script-time execution checks.

    Self-contained rather than imported from tests/backtester/conftest.py's
    make_synthetic_data: scripts/ depending on tests/ would invert the intended
    dependency direction (tests exercise src/, not the other way around). Column
    names are lowercase to match how these scripts call pandas-ta functions
    directly (ta.rsi(close), not through backtesting.py's capitalized
    Open/High/Low/Close convention).

    n_bars defaults to 1500 (~6 years of trading days), not a smaller round number:
    confirmed empirically that 300 bars was too small relative to real, sane LLM-
    proposed bounds (e.g. length=252, a standard "one trading year" bound) -- dpo,
    among others, silently returned None or raised "Length of values (0) does not
    match length of index" purely from insufficient warmup room, not from anything
    wrong with the proposed bounds or the function itself. 1500 bars gives genuine
    headroom for bounds on the order of a few hundred, which is realistic: rules
    run against years of real daily data in production, so testing against a
    dataset of that rough scale is more representative anyway, not just a workaround.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n_bars)
    daily_returns = rng.normal(0.0005, 0.012, n_bars)
    close_vals = 100.0 * np.exp(np.cumsum(daily_returns))

    close = pd.Series(close_vals, index=idx)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.Series(np.maximum(close_vals, open_.to_numpy()) * rng.uniform(1.000, 1.010, n_bars), index=idx)
    low = pd.Series(np.minimum(close_vals, open_.to_numpy()) * rng.uniform(0.990, 1.000, n_bars), index=idx)
    volume = pd.Series(rng.integers(500_000, 2_000_000, n_bars).astype(float), index=idx)

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def param_midpoint(bounds: tuple[float, float]) -> float:
    """Rounded to the nearest whole number, not the exact arithmetic midpoint.

    Confirmed necessary, not cosmetic: many pandas-ta params (length-like: p, q,
    fast, slow, ...) require a genuine int internally. normalize_params() only
    converts WHOLE-valued floats to int (5.0 -> 5); an exact midpoint like
    (5.0 + 100.0) / 2 = 52.5 stays a non-whole float and breaks functions doing
    integer-only slicing (confirmed for real: cksp's `p` baseline at 52.5 raised
    "cannot do slice indexing... with these indexers [0] of type int"). Rounding
    first, then letting normalize_params do its job, fixes integer-only params and
    is safe for genuinely fractional params too -- same "whole int vs whole float
    are numerically identical" precedent normalize_params itself already relies
    on, just with a little precision loss for continuous params like `x` (never
    asserted to be more precise than "roughly centered" in the first place).

    Shared by generate_extended_indicators.py (a third sample point for deriving
    column prefixes -- see indicators._derive_column_prefixes) and
    verify_extended_indicators.py (the "hold other params at baseline" value for
    per-param sensitivity and cross-check tests), so both scripts' idea of
    "the middle of this range" always agrees.
    """
    lo, hi = bounds
    return round((lo + hi) / 2)


def _format_value(value: object) -> str:
    """repr(), except dates render as date(y, m, d) instead of datetime.date(...) --
    keeps the generated file's own `from datetime import date` import sufficient."""
    if isinstance(value, date):
        return f"date({value.year}, {value.month}, {value.day})"
    return repr(value)


_MODULE_HEADER = '''"""Extended-tier indicator registry -- AUTO-GENERATED, do not hand-edit.

Produced by `scripts/generate_extended_indicators.py` (structure + LLM-proposed
parameter bounds) and rewritten in place by `scripts/verify_extended_indicators.py`
(flips verified/verified_on/lib_version per entry after execution-based checks). To
regenerate from scratch, rerun both scripts in that order.
"""

from datetime import date

import pandas_ta as ta

from .indicators import IndicatorSpec

EXTENDED_INDICATORS: dict[str, IndicatorSpec] = {'''


def render_extended_indicators_module(specs: dict[str, IndicatorSpec]) -> str:
    """The one place that turns {name: IndicatorSpec} into extended_indicators.py's
    source text. Every field is spelled out explicitly (not routed through the
    _extended() convenience builder) so the generated file is fully self-describing
    and re-renderable from its own dataclass state alone."""
    lines = [_MODULE_HEADER]
    for name in sorted(specs):
        spec = specs[name]
        lines.append(f'    "{name}": IndicatorSpec(')
        lines.append(f"        fn=ta.{spec.fn.__name__},")
        lines.append(f"        inputs={spec.inputs!r},")
        lines.append(f"        params={spec.params!r},")
        lines.append(f"        column_prefix={spec.column_prefix!r},")
        lines.append(f"        cross_check={spec.cross_check!r},")
        lines.append('        tier="extended",')
        lines.append(f"        verified={spec.verified!r},")
        lines.append(f"        verified_on={_format_value(spec.verified_on)},")
        lines.append(f"        lib_version={spec.lib_version!r},")
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
