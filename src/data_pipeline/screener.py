"""Universe screener: relative-threshold ticker filtering.

Thresholds are RELATIVE (a percentile within a group), never a hand-picked
absolute number — .claude/rules/data-pipeline.md's own rule. Metadata
filters (sector, industry) are lookups against TickerMetadata. Computed
filters (liquidity, volatility) are computed from PriceBar, using only data
as of a given reference date — point-in-time by construction, the same
"screening on today's data to backtest from 2015 uses future information"
concern architecture.md §5 names for universe selection, one layer above
where regime.py already applies the identical discipline per-bar.

Metadata (sector/industry) is NOT point-in-time — TickerMetadata has no
history, only a current snapshot, so a sector filter always reflects
whatever the most recent ingestion said, regardless of as_of. Disclosed
here rather than glossed over, the same "measure the gap and disclose it"
treatment architecture.md §6 already gives survivorship bias.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import PriceBar, TickerMetadata

_MIN_OBSERVATIONS = 5  # fewer than this and a metric isn't meaningfully comparable


class ScreenerCandidate(BaseModel):
    ticker: str
    sector: str | None
    industry: str | None
    metric_value: float
    percentile: float  # 0-100: 100 = highest metric_value in the group, 0 = lowest


class ScreenerResult(BaseModel):
    group_size: int
    candidates: list[ScreenerCandidate]


def _metric_value(session: Session, ticker: str, metric: str, lookback_days: int, as_of: date | None) -> float | None:
    """The metric's value over ticker's trailing `lookback_days` price rows as of as_of.

    Returns None if fewer than _MIN_OBSERVATIONS rows are available in the window —
    excluded from the group entirely rather than given a value computed on too
    little data to be meaningful.
    """
    stmt = select(PriceBar.adj_close, PriceBar.adj_volume).where(PriceBar.ticker == ticker)
    if as_of is not None:
        stmt = stmt.where(PriceBar.date <= as_of)
    stmt = stmt.order_by(PriceBar.date.desc()).limit(lookback_days)
    rows = session.execute(stmt).all()
    if len(rows) < _MIN_OBSERVATIONS:
        return None

    close = pd.Series([float(r.adj_close) for r in reversed(rows)])
    if metric == "liquidity":
        volume = pd.Series([float(r.adj_volume) for r in reversed(rows)])
        return float((close * volume).mean())
    if metric == "volatility":
        returns = close.pct_change().dropna()
        if len(returns) < 2:
            return None
        return float(returns.std())
    raise ValueError(f"unknown metric {metric!r}")


def screen(
    session: Session,
    sector: str | None = None,
    industry: str | None = None,
    metric: Literal["liquidity", "volatility"] = "liquidity",
    lookback_days: int = 63,
    as_of: date | None = None,
) -> ScreenerResult:
    """Rank tickers matching sector/industry by metric, relative to the group.

    percentile is computed within the matched group only — "lowest volatility
    quintile within the sector" means percentile <= 20 among tickers sharing
    that sector, not against the whole universe.
    """
    stmt = select(TickerMetadata)
    if sector is not None:
        stmt = stmt.where(TickerMetadata.sector == sector)
    if industry is not None:
        stmt = stmt.where(TickerMetadata.industry == industry)
    metas = {m.ticker: m for m in session.execute(stmt).scalars().all()}

    values: dict[str, float] = {}
    for ticker in metas:
        value = _metric_value(session, ticker, metric, lookback_days, as_of)
        if value is not None:
            values[ticker] = value

    if not values:
        return ScreenerResult(group_size=0, candidates=[])

    ranked = sorted(values, key=lambda t: values[t], reverse=True)
    n = len(ranked)

    candidates = []
    for rank, ticker in enumerate(ranked):
        percentile = 100 * (n - 1 - rank) / (n - 1) if n > 1 else 100.0
        meta = metas[ticker]
        candidates.append(
            ScreenerCandidate(
                ticker=ticker,
                sector=meta.sector,
                industry=meta.industry,
                metric_value=values[ticker],
                percentile=percentile,
            )
        )

    return ScreenerResult(group_size=n, candidates=candidates)
