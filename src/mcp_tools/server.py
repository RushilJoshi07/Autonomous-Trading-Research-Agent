from datetime import date

import pandas as pd
from mcp.server import MCPServer

from backtester.data_loader import load_price_data
from backtester.engine import run_backtest as _run_backtest
from backtester.indicator_compute import compute_indicator as _compute_indicator
from backtester.regime import classify_regime as _classify_regime
from backtester.registry import ALL_INDICATORS
from backtester.result import BacktestResult
from backtester.schema import StrategyRule
from backtester.strategies.rule_strategy import make_rule_strategy
from data_pipeline.db.session import SessionFactory
from mcp_tools.schemas import IndicatorInfo, IndicatorValueOut, PriceBarOut, RegimeRecordOut

mcp = MCPServer("agentic-finance-platform")


@mcp.tool()
def get_price_data(ticker: str, start: date | None = None, end: date | None = None) -> list[PriceBarOut]:
    """Daily OHLCV bars for a ticker from the cached database (splits/dividends adjusted)."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
    return [
        PriceBarOut(
            date=row.Index.date(),
            open=row.Open,
            high=row.High,
            low=row.Low,
            close=row.Close,
            volume=int(row.Volume),
        )
        for row in df.itertuples()
    ]


@mcp.tool()
def run_backtest(
    rule: StrategyRule,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    commission: float | None = None,
    cash: float | None = None,
) -> BacktestResult:
    """Backtest a StrategyRule against a ticker's cached price history."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
    strategy_cls = make_rule_strategy(rule)
    kwargs = {}
    if commission is not None:
        kwargs["commission"] = commission
    if cash is not None:
        kwargs["cash"] = cash
    return _run_backtest(df, strategy_cls, ticker=ticker, **kwargs)


@mcp.tool()
def compute_indicator(
    ticker: str,
    name: str,
    params: dict[str, float] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[IndicatorValueOut]:
    """Compute a registered indicator's full time series for a ticker."""
    with SessionFactory() as session:
        series = _compute_indicator(ticker, name, params or {}, session, start=start, end=end)
    return [
        IndicatorValueOut(date=idx.date(), value=float(val))
        for idx, val in series.items()
        if pd.notna(val)
    ]


@mcp.tool()
def list_indicators() -> list[IndicatorInfo]:
    """Every indicator usable by compute_indicator or a StrategyRule, with tier, verification status, and parameter bounds."""
    return [
        IndicatorInfo(name=name, tier=spec.tier, verified=spec.verified, inputs=list(spec.inputs), params=spec.params)
        for name, spec in sorted(ALL_INDICATORS.items())
    ]


@mcp.tool()
def classify_regime(ticker: str, start: date | None = None, end: date | None = None) -> list[RegimeRecordOut]:
    """Label each bar's trend strength and volatility level, relative to its own trailing 252-bar history."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=None, end=end)
    regimes = _classify_regime(df)
    if start is not None:
        regimes = regimes[regimes.index >= pd.Timestamp(start)]
    return [
        RegimeRecordOut(
            date=row.Index.date(),
            adx_percentile=None if pd.isna(row.adx_percentile) else float(row.adx_percentile),
            trend_regime=row.trend_regime,
            natr_percentile=None if pd.isna(row.natr_percentile) else float(row.natr_percentile),
            vol_regime=row.vol_regime,
        )
        for row in regimes.itertuples()
    ]


if __name__ == "__main__":
    mcp.run()
