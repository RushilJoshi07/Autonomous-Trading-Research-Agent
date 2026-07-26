import pandas as pd
from backtesting import Backtest, Strategy

from backtester.result import BacktestResult

_DEFAULT_COMMISSION = 0.001  # 0.1% per trade
_DEFAULT_CASH = 10_000


def run_backtest(
    data: pd.DataFrame,
    strategy_cls: type[Strategy],
    ticker: str = "",
    commission: float = _DEFAULT_COMMISSION,
    cash: float = _DEFAULT_CASH,
    trade_on_close: bool = False,
    **strategy_params,
) -> BacktestResult:
    """Run a backtest and return a structured result.

    data           — DataFrame from load_price_data (Open/High/Low/Close/Volume,
                     DatetimeIndex). May include extra signal columns.
    strategy_cls   — a backtesting.Strategy subclass.
    ticker         — passed through to BacktestResult for labelling.
    commission     — fraction per trade (e.g. 0.001 = 0.1%).
    cash           — starting equity.
    trade_on_close — if True, orders execute at the current bar's close rather
                     than the next bar's open. Required when a signal is computed
                     from the same bar's close (e.g. a pre-shifted lookahead column).
    **strategy_params — forwarded to bt.run() as strategy parameter overrides.
    """
    bt = Backtest(
        data,
        strategy_cls,
        commission=commission,
        cash=cash,
        exclusive_orders=True,
        finalize_trades=True,
        trade_on_close=trade_on_close,
    )
    stats = bt.run(**strategy_params)
    return BacktestResult.from_stats(stats, ticker=ticker, commission=commission)
