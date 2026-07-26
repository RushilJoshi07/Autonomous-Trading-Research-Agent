import math

from backtester.data_loader import load_price_data
from backtester.engine import run_backtest
from backtester.result import BacktestResult
from backtester.strategies.sma_crossover import SMACrossover


def test_result_has_all_fields(seeded_db):
    """run_backtest returns a valid BacktestResult with all fields populated."""
    data = load_price_data("SYNTHETIC", seeded_db)
    result = run_backtest(data, SMACrossover, ticker="SYNTHETIC")

    assert isinstance(result, BacktestResult)
    assert result.ticker == "SYNTHETIC"
    assert result.commission_pct == 0.001
    assert result.num_trades > 0
    assert math.isfinite(result.sharpe_ratio)
    assert math.isfinite(result.max_drawdown_pct)
    assert math.isfinite(result.annual_return_pct)
    assert math.isfinite(result.total_return_pct)
    assert 0.0 <= result.win_rate_pct <= 100.0


def test_max_drawdown_is_non_positive(seeded_db):
    """max_drawdown_pct must be ≤ 0. A positive value means the stat is being read wrong."""
    data = load_price_data("SYNTHETIC", seeded_db)
    result = run_backtest(data, SMACrossover, ticker="SYNTHETIC")
    assert result.max_drawdown_pct <= 0.0


def test_strategy_params_override(seeded_db):
    """Fast and slow period overrides are forwarded to the strategy."""
    data = load_price_data("SYNTHETIC", seeded_db)
    # Very tight periods → many more crossovers → more trades
    result_tight = run_backtest(
        data, SMACrossover, ticker="SYNTHETIC", fast_period=3, slow_period=7
    )
    result_default = run_backtest(data, SMACrossover, ticker="SYNTHETIC")
    assert result_tight.num_trades >= result_default.num_trades
