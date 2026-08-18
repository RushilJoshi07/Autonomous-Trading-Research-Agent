from datetime import date
from typing import Literal

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
from data_pipeline.screener import ScreenerResult
from data_pipeline.screener import screen as _screen
from mcp_tools.schemas import IndicatorInfo, IndicatorValueOut, PriceBarOut, RegimeRecordOut
from research_stats.confidence import ConfidenceIntervalResult
from research_stats.confidence import bootstrap_ci as _bootstrap_ci
from research_stats.multiple_comparisons import MultipleComparisonsResult
from research_stats.multiple_comparisons import correct_p_values as _correct_p_values
from research_stats.significance import SignificanceResult
from research_stats.significance import test_significance as _test_significance

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


@mcp.tool()
def test_significance(
    rule: StrategyRule,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    commission: float | None = None,
    cash: float | None = None,
    n_resamples: int = 300,
    seed: int = 0,
) -> SignificanceResult:
    """Test whether a strategy beats randomized entries at the same trade frequency (Monte Carlo permutation test, no distributional assumption)."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
    return _test_significance(
        df, rule, ticker=ticker, commission=commission, cash=cash, n_resamples=n_resamples, seed=seed
    )


@mcp.tool()
def confidence_interval(
    rule: StrategyRule,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    commission: float | None = None,
    cash: float | None = None,
    confidence_level: float = 0.95,
    seed: int = 0,
) -> ConfidenceIntervalResult:
    """Bootstrap confidence interval for a strategy's mean per-trade return, resampled at the trade level (not daily bars)."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
    strategy_cls = make_rule_strategy(rule)
    kwargs = {}
    if commission is not None:
        kwargs["commission"] = commission
    if cash is not None:
        kwargs["cash"] = cash
    result = _run_backtest(df, strategy_cls, ticker=ticker, **kwargs)
    return _bootstrap_ci(result.trade_returns, confidence_level=confidence_level, seed=seed)


@mcp.tool()
def correct_p_values(p_values: list[float], method: str = "bh") -> MultipleComparisonsResult:
    """Adjust a list of p-values for multiple comparisons (Benjamini-Hochberg by default)."""
    return _correct_p_values(p_values, method=method)


@mcp.tool()
def screen_universe(
    sector: str | None = None,
    industry: str | None = None,
    metric: Literal["liquidity", "volatility"] = "liquidity",
    lookback_days: int = 63,
    as_of: date | None = None,
) -> ScreenerResult:
    """Rank tickers by relative liquidity or volatility percentile within a sector/industry group, computed only from data as of a given date (point-in-time — no lookahead into universe selection)."""
    with SessionFactory() as session:
        return _screen(session, sector=sector, industry=industry, metric=metric, lookback_days=lookback_days, as_of=as_of)


if __name__ == "__main__":
    mcp.run()
