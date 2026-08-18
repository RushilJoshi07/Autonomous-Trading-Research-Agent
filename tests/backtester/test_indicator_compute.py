"""Tests for indicator_compute.py -- standalone indicator computation,
independent of a running backtesting.py Strategy.

Component 8 (Stage 4) formal coverage for Components 4 and 5's shared
computation core (compute_indicator_series) and Component 4's ticker-based
wrapper (compute_indicator).
"""

import numpy as np
import pytest

from backtester.indicator_compute import compute_indicator, compute_indicator_series


def test_compute_indicator_series_matches_independent_computation(synthetic_data):
    """SMA(10) via compute_indicator_series must match a plain pandas rolling
    mean computed independently -- the same "verify by an independent
    reproduction" discipline Stage 3's gate script used for rsi_14_30_70, not
    just checking that some numeric Series comes back."""
    series = compute_indicator_series(synthetic_data, "SMA", {"length": 10})
    expected = synthetic_data["Close"].rolling(10).mean()
    assert np.allclose(series.to_numpy(), expected.to_numpy(), equal_nan=True)


def test_compute_indicator_series_unknown_indicator_raises(synthetic_data):
    with pytest.raises(ValueError, match="unknown indicator"):
        compute_indicator_series(synthetic_data, "NOTREAL", {})


def test_compute_indicator_series_out_of_bounds_param_raises(synthetic_data):
    with pytest.raises(ValueError, match="out of bounds"):
        compute_indicator_series(synthetic_data, "SMA", {"length": 99999})


def test_compute_indicator_multi_output_selects_correct_column(synthetic_data):
    """ADX's underlying ta.adx() returns a 3-column DataFrame (ADX/DMP/DMN,
    disambiguated by column_prefix). Regression coverage for the exact
    select_output_column mechanism compute_indicator_series depends on --
    a wrong prefix match would silently return a DIFFERENT column's values,
    not raise, so this checks the result is bounded like ADX actually is
    (0-100), not just "some Series came back"."""
    series = compute_indicator_series(synthetic_data, "ADX", {"length": 14})
    valid = series.dropna()
    assert len(valid) > 0
    assert valid.min() >= 0 and valid.max() <= 100


def test_compute_indicator_delegates_to_series_without_changing_values(seeded_db, synthetic_data):
    """compute_indicator (ticker-based, reads from the DB) must produce values
    matching compute_indicator_series (DataFrame-based) computed directly on
    the same underlying data -- proving Component 5's refactor split the
    original compute_indicator into two functions without changing behavior.
    Uses allclose with a small tolerance, not exact equality: price data
    round-trips through Numeric(18,6) DB columns, which can introduce
    sub-millionth rounding differences exact equality would flag as a false
    failure."""
    from_ticker = compute_indicator("SYNTHETIC", "RSI", {"length": 14}, seeded_db)
    from_frame = compute_indicator_series(synthetic_data, "RSI", {"length": 14})
    assert np.allclose(from_ticker.to_numpy(), from_frame.to_numpy(), equal_nan=True, atol=1e-4)
