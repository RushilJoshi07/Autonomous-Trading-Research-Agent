from datetime import date

from pydantic import BaseModel


class BacktestResult(BaseModel):
    ticker: str
    start: date
    end: date
    sharpe_ratio: float
    max_drawdown_pct: float
    annual_return_pct: float
    total_return_pct: float
    num_trades: int
    win_rate_pct: float
    commission_pct: float

    @classmethod
    def from_stats(
        cls,
        stats: "pd.Series",  # noqa: F821 — avoid importing pandas at module level
        ticker: str,
        commission: float,
    ) -> "BacktestResult":
        """Parse a backtesting.py stats Series into a BacktestResult."""
        def _f(key: str) -> float:
            v = stats.get(key, float("nan"))
            try:
                return float(v)
            except (TypeError, ValueError):
                return float("nan")

        return cls(
            ticker=ticker,
            start=stats["Start"].date() if hasattr(stats["Start"], "date") else stats["Start"],
            end=stats["End"].date() if hasattr(stats["End"], "date") else stats["End"],
            sharpe_ratio=_f("Sharpe Ratio"),
            max_drawdown_pct=_f("Max. Drawdown [%]"),
            annual_return_pct=_f("Return (Ann.) [%]"),
            total_return_pct=_f("Return [%]"),
            num_trades=int(stats.get("# Trades", 0)),
            win_rate_pct=_f("Win Rate [%]"),
            commission_pct=commission,
        )
