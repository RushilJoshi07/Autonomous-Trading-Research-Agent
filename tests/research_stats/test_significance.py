"""Tests for research_stats/significance.py -- the Monte Carlo significance
test against a randomized-entry control.

Component 8 (Stage 4) formal coverage for Component 6.
"""

import numpy as np
import pandas as pd
import pytest

from backtester.schema import KNOWN_STRATEGIES, Comparison, Condition, ConstantTerm, IndicatorTerm, StrategyRule
from research_stats.significance import test_significance as run_significance_test

_N_RESAMPLES = 20  # small on purpose -- these are correctness checks, not precision checks


def test_p_value_is_a_valid_probability(synthetic_data):
    result = run_significance_test(synthetic_data, KNOWN_STRATEGIES["sma_10_30_crossover"], ticker="SYNTHETIC", n_resamples=_N_RESAMPLES, seed=0)
    assert 0.0 <= result.p_value <= 1.0
    assert result.observed_num_trades > 0
    assert result.n_resamples == _N_RESAMPLES


def test_real_scipy_rvs_tuple_shaped_size_is_handled(synthetic_data):
    """Regression test for a real bug: scipy.stats.monte_carlo_test calls rvs
    with size as a TUPLE (e.g. (20, 1)), not a plain int -- confirmed by
    direct testing during Component 6's build, after an offline toy check
    (which used numpy's tuple-tolerant `size=` parameter) failed to catch it.
    This isn't a mock or a reimplementation of the fix in isolation -- it's a
    real call to test_significance, which is the only way rvs's handling of
    scipy's actual size argument shape is genuinely exercised. If the
    isinstance(size, tuple) handling in significance.py's rvs regressed back
    to assuming size is always a plain int, this call would fail immediately
    with "'tuple' object cannot be interpreted as an integer" -- it doesn't."""
    result = run_significance_test(synthetic_data, KNOWN_STRATEGIES["sma_10_30_crossover"], ticker="SYNTHETIC", n_resamples=_N_RESAMPLES, seed=0)
    assert result.n_resamples == _N_RESAMPLES


def test_same_seed_gives_identical_p_value(synthetic_data):
    rule = KNOWN_STRATEGIES["sma_10_30_crossover"]
    a = run_significance_test(synthetic_data, rule, ticker="SYNTHETIC", n_resamples=_N_RESAMPLES, seed=5)
    b = run_significance_test(synthetic_data, rule, ticker="SYNTHETIC", n_resamples=_N_RESAMPLES, seed=5)
    assert a.p_value == b.p_value
    assert a.null_mean_sharpe == b.null_mean_sharpe


def test_zero_real_trades_raises():
    """A rule whose entry condition can structurally never fire (RSI is
    bounded [0,100]; crosses_below -100 is unreachable) must be rejected
    before any random control is attempted -- there's no "same trade
    frequency" to compare against when the real frequency is zero."""
    rule = StrategyRule(
        name="impossible",
        description="RSI can never cross below -100",
        entry=Condition(kind="leaf", comparison=Comparison(
            left=IndicatorTerm(name="RSI", params={"length": 14}), op="crosses_below", right=ConstantTerm(value=-100))),
        exit_after_bars=5,
    )
    rng = np.random.default_rng(1)
    n = 300
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    df = pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1_000_000.0},
        index=pd.bdate_range("2020-01-01", periods=n, name="Date"),
    )
    with pytest.raises(ValueError, match="produced 0 trades"):
        run_significance_test(df, rule, ticker="SYNTHETIC", n_resamples=_N_RESAMPLES, seed=0)
