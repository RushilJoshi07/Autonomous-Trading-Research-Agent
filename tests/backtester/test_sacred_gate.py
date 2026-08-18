"""Sacred Gate 1 verification tests.

Gate 1a — no lookahead bias:
  Deliberately pass a pre-shifted column (Signal = tomorrow's direction) to
  the backtester. Prove the Sharpe is implausibly high. Prove a clean strategy
  on the same data has a much lower Sharpe. This shows that: (a) lookahead
  data reaching the backtester produces detectably wrong results; (b) clean
  data produces realistic numbers. The structural prevention is that
  load_price_data reads stored OHLCV columns — no shifting ever happens there.

Gate 1b — transaction costs change outcomes:
  Run SMACrossover twice on the same data: once with no commission, once with
  0.2% per trade. Net return must be strictly lower with costs.
"""
import numpy as np
import pandas as pd
import pytest
from backtesting import Strategy
from backtesting.lib import crossover

from backtester.engine import run_backtest
from backtester.strategies.sma_crossover import SMACrossover
from tests.conftest import make_synthetic_data


# ---------------------------------------------------------------------------
# Lookahead strategy (defined here — it is intentionally broken and belongs
# only in tests, not in src/)
# ---------------------------------------------------------------------------

class LookaheadStrategy(Strategy):
    """Buys when it 'knows' tomorrow's close will be higher than today's.

    The Signal column is pre-computed with shift(-1) before the data reaches
    the backtester. backtesting.py cannot detect this — it treats Signal like
    any other column. The implausibly high Sharpe is the detection mechanism.
    """

    def init(self):
        # Wrap the pre-shifted Signal column as a tracked indicator.
        # self.I records it for plots/stats but does not alter its values.
        self.signal = self.I(lambda x: x, self.data.Signal, name="lookahead_signal")

    def next(self):
        if self.signal[-1] > 0.5 and not self.position:
            self.buy()
        elif self.signal[-1] <= 0.5 and self.position:
            self.position.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bt_data(seed: int = 42, n_bars: int = 500) -> pd.DataFrame:
    """Return a deterministic OHLCV DataFrame for backtesting.py."""
    rng = np.random.default_rng(seed)
    daily_returns = rng.normal(0.0005, 0.012, n_bars)
    close = 100.0 * np.exp(np.cumsum(daily_returns))
    dates = pd.bdate_range("2020-01-01", periods=n_bars)
    return pd.DataFrame(
        {
            "Open":   close * rng.uniform(0.997, 1.000, n_bars),
            "High":   close * rng.uniform(1.000, 1.010, n_bars),
            "Low":    close * rng.uniform(0.990, 1.000, n_bars),
            "Close":  close,
            "Volume": rng.integers(500_000, 2_000_000, n_bars).astype(float),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def _add_lookahead_signal(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with a Signal column that encodes tomorrow's direction.

    Signal[i] = 1.0 when close[i+1] > close[i], else 0.0.
    The last row gets 0.0 (no future to peek at — stay flat).
    This is the attack: future-knowing data slipped into the DataFrame
    before it reaches the backtester.
    """
    poisoned = df.copy()
    poisoned["Signal"] = (df["Close"].shift(-1) > df["Close"]).astype(float).fillna(0.0)
    return poisoned


# ---------------------------------------------------------------------------
# Gate 1a — lookahead detection
# ---------------------------------------------------------------------------

def test_lookahead_sharpe_is_implausibly_high():
    """A strategy fed future-knowing data must produce a Sharpe > 3.0.

    Real strategies on daily equity data rarely exceed Sharpe 2.0. A perfect
    direction predictor will far exceed that. If this assertion fails, the
    lookahead strategy is not actually seeing future data (the test is broken),
    not that the backtester is safe.
    """
    data = _add_lookahead_signal(_make_bt_data())
    result = run_backtest(data, LookaheadStrategy, ticker="LOOKAHEAD", commission=0.0, trade_on_close=True)
    assert result.sharpe_ratio > 3.0, (
        f"Lookahead Sharpe {result.sharpe_ratio:.2f} is not implausibly high. "
        "The LookaheadStrategy is not actually seeing future data — check the "
        "Signal column construction or the strategy's init/next logic."
    )
    assert result.num_trades > 50, (
        f"Only {result.num_trades} trades — the strategy barely traded. "
        "Confirm the Signal column has the right values."
    )


def test_clean_strategy_sharpe_is_lower_than_lookahead():
    """SMACrossover on clean data must have a much lower Sharpe than the lookahead.

    This confirms that the clean strategy is not accidentally seeing future data.
    If both Sharpes are similar, something is wrong with the lookahead test setup.
    """
    clean_data = _make_bt_data()
    poisoned_data = _add_lookahead_signal(clean_data)

    clean_result = run_backtest(clean_data, SMACrossover, ticker="CLEAN", commission=0.0)
    lookahead_result = run_backtest(poisoned_data, LookaheadStrategy, ticker="LOOKAHEAD", commission=0.0, trade_on_close=True)

    assert clean_result.sharpe_ratio < lookahead_result.sharpe_ratio, (
        f"Clean Sharpe ({clean_result.sharpe_ratio:.2f}) is not lower than "
        f"lookahead Sharpe ({lookahead_result.sharpe_ratio:.2f}). "
        "The gate assertion is not discriminating between clean and lookahead strategies."
    )


# ---------------------------------------------------------------------------
# Gate 1b — transaction costs change outcomes
# ---------------------------------------------------------------------------

def test_costs_reduce_returns():
    """Running SMACrossover with commission=0.002 must produce a lower return than 0.0.

    This proves costs are actually applied and not silently dropped. We use 0.2%
    (double the default) to ensure the effect is large enough to be unambiguous
    on 500 bars with moderate trade frequency.
    """
    data = _make_bt_data()
    result_no_cost = run_backtest(data, SMACrossover, ticker="NOCOST", commission=0.0)
    result_with_cost = run_backtest(data, SMACrossover, ticker="WITHCOST", commission=0.002)

    assert result_with_cost.total_return_pct < result_no_cost.total_return_pct, (
        f"Return with costs ({result_with_cost.total_return_pct:.2f}%) is not lower "
        f"than without costs ({result_no_cost.total_return_pct:.2f}%). "
        "Transaction costs are not being applied."
    )
    assert result_with_cost.commission_pct == 0.002
    assert result_no_cost.commission_pct == 0.0
