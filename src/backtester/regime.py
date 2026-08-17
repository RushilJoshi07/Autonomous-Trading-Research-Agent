"""Per-bar regime labeling: trend strength and volatility level, relative to
each ticker's own trailing history rather than a hand-picked absolute level.

Reuses the core registry (ADX for trend strength, NATR for volatility) via
indicator_compute.compute_indicator_series -- no new indicator math.
"""

import pandas as pd

from .indicator_compute import compute_indicator_series

# 252 trading days (~1 calendar year) -- the same annualization convention
# backtesting.py's own Return (Ann.) [%] already uses elsewhere in this project.
# Trailing (pandas' rolling() default, center=False), never expanding/full-sample:
# an expanding window would let an early bar's classification depend on data
# from years later than that bar -- the same lookahead shape architecture.md
# warns against for point-in-time universe selection, one layer down at the
# per-bar level.
REGIME_LOOKBACK_BARS = 252

_TREND_PARAMS = {"length": 14}  # ADX's own pandas-ta default; within its registered [2, 100] bound
_VOL_PARAMS = {"length": 14}  # NATR's own default; same bound

_TREND_LABELS = ("choppy", "neutral", "trending")
_VOL_LABELS = ("low_vol", "neutral", "high_vol")


def _tercile_label(pct: float, labels: tuple[str, str, str]) -> str:
    low, mid, high = labels
    if pct < 100 / 3:
        return low
    if pct > 200 / 3:
        return high
    return mid


def classify_regime(price_data: pd.DataFrame) -> pd.DataFrame:
    """Label each bar's trend strength and volatility level.

    Both labels are tercile splits of the indicator's own rolling 252-bar
    percentile rank, not a fixed absolute threshold -- self-calibrating per
    ticker and per era, per .claude/rules/data-pipeline.md's "thresholds are
    relative, never hand-picked" rule.

    Bars before the 252nd in price_data have no defined regime -- both labels
    read "insufficient_history" and both percentiles are NaN, rather than a
    quantile computed on too little data to be meaningful.
    """
    adx = compute_indicator_series(price_data, "ADX", _TREND_PARAMS)
    natr = compute_indicator_series(price_data, "NATR", _VOL_PARAMS)

    adx_pct = adx.rolling(REGIME_LOOKBACK_BARS).rank(pct=True) * 100
    natr_pct = natr.rolling(REGIME_LOOKBACK_BARS).rank(pct=True) * 100

    trend_regime = adx_pct.apply(
        lambda p: "insufficient_history" if pd.isna(p) else _tercile_label(p, _TREND_LABELS)
    )
    vol_regime = natr_pct.apply(
        lambda p: "insufficient_history" if pd.isna(p) else _tercile_label(p, _VOL_LABELS)
    )

    return pd.DataFrame(
        {
            "adx_percentile": adx_pct,
            "trend_regime": trend_regime,
            "natr_percentile": natr_pct,
            "vol_regime": vol_regime,
        },
        index=price_data.index,
    )
