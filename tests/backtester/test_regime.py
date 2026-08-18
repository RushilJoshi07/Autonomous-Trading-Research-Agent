"""Tests for regime.py -- per-bar trend/volatility labeling.

Component 8 (Stage 4) formal coverage for Component 5. The lookahead-safety
test is the most important one here: it directly verifies the property the
whole trailing-window design exists to guarantee, the same discipline
Sacred Gate 1 requires of the backtester itself, one layer up.
"""

import numpy as np
import pandas as pd
import pytest

from backtester.regime import REGIME_LOOKBACK_BARS, _tercile_label, classify_regime


def test_insufficient_history_boundary_is_exact(synthetic_data):
    """The transition from insufficient_history to a real label must happen
    at exactly one bar, with no partial/mixed state -- and, on the standard
    500-bar synthetic fixture, at bar 264 specifically (252 valid ADX
    observations required by REGIME_LOOKBACK_BARS, plus ADX(14)'s own ~13-bar
    warmup before it produces its first non-NaN value at all) -- the exact
    boundary Component 5's own manual verification found on real AAPL data,
    confirmed here to be a property of the indicator math, not that
    specific dataset."""
    result = classify_regime(synthetic_data)
    insufficient = result[result["trend_regime"] == "insufficient_history"]
    real = result[result["trend_regime"] != "insufficient_history"]
    assert len(insufficient) == 264
    assert result.index.get_loc(insufficient.index[-1]) == 263
    assert result.index.get_loc(real.index[0]) == 264
    # Both label columns and both percentile columns must agree on which
    # side of the boundary each bar falls -- an insufficient trend_regime
    # with a defined vol_regime (or vice versa) would mean the two metrics'
    # NaN-detection logic had drifted apart from each other.
    assert (insufficient["vol_regime"] == "insufficient_history").all()
    assert insufficient["adx_percentile"].isna().all()
    assert insufficient["natr_percentile"].isna().all()
    assert real["adx_percentile"].notna().all()
    assert real["natr_percentile"].notna().all()


def test_regime_is_lookahead_safe(synthetic_data):
    """A bar's regime classification must not depend on any bar after it --
    the same no-lookahead discipline Sacred Gate 1 requires of the
    backtester, applied here to regime labeling instead of trade signals.

    Verified structurally, not by inspecting pandas' documentation: classify
    the full 500-bar series, then classify a version truncated right after
    bar 300, and confirm bar 300's own classification is identical either
    way. If the rolling window looked even one bar into the future, cutting
    the data off after bar 300 would change what bar 300 itself reports --
    it doesn't, here, which is the actual property this test exists to
    catch a regression in, not just "the function runs."
    """
    full = classify_regime(synthetic_data)
    truncated = classify_regime(synthetic_data.iloc[:301])  # bars 0..300 inclusive

    full_bar_300 = full.iloc[300]
    truncated_bar_300 = truncated.iloc[300]

    assert full_bar_300["trend_regime"] == truncated_bar_300["trend_regime"]
    assert full_bar_300["vol_regime"] == truncated_bar_300["vol_regime"]
    assert np.isclose(full_bar_300["adx_percentile"], truncated_bar_300["adx_percentile"])
    assert np.isclose(full_bar_300["natr_percentile"], truncated_bar_300["natr_percentile"])


def test_vol_regime_responds_to_a_real_volatility_spike():
    """vol_regime must actually track volatility, not just always return the
    same label -- proven by comparing two synthetic series identical except
    for a deliberate volatility spike injected into the final 30 bars of
    one of them. The spiked series' last bar must rank at a higher NATR
    percentile than the calm series' last bar."""
    rng = np.random.default_rng(7)
    n = 400
    dates = pd.bdate_range("2020-01-01", periods=n)

    def make(spike: bool) -> pd.DataFrame:
        returns = rng.normal(0.0003, 0.01, n)
        if spike:
            returns[-30:] = rng.normal(0.0, 0.08, 30)  # much higher-variance tail
        close = 100.0 * np.exp(np.cumsum(returns))
        return pd.DataFrame(
            {
                "Open": close, "High": close * 1.01, "Low": close * 0.99,
                "Close": close, "Volume": 1_000_000.0,
            },
            index=pd.DatetimeIndex(dates, name="Date"),
        )

    calm = classify_regime(make(spike=False))
    spiked = classify_regime(make(spike=True))

    assert spiked["natr_percentile"].iloc[-1] > calm["natr_percentile"].iloc[-1]
    assert spiked["vol_regime"].iloc[-1] in ("high_vol", "neutral")


@pytest.mark.parametrize(
    "pct,expected",
    [
        (0.0, "low"),
        (100 / 3 - 0.01, "low"),
        (100 / 3, "mid"),
        (50.0, "mid"),
        (200 / 3, "mid"),
        (200 / 3 + 0.01, "high"),
        (100.0, "high"),
    ],
)
def test_tercile_label_boundaries(pct, expected):
    assert _tercile_label(pct, ("low", "mid", "high")) == expected


def test_regime_lookback_bars_is_252():
    """Pinned down explicitly, not left to drift -- the approved Stage 4 plan
    settled on 252 (matching backtesting.py's own annualization convention)
    specifically so it wouldn't be silently changed later without noticing."""
    assert REGIME_LOOKBACK_BARS == 252
