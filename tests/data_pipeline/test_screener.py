"""Tests for screener.py -- relative-threshold ticker filtering.

Component 8 (Stage 4) formal coverage for Component 7. Uses hand-built
synthetic price series with deliberately controlled liquidity/volatility
characteristics (not the shared 500-bar synthetic_data fixture, which has
no way to control per-ticker volume or a mid-series volatility change) so
every assertion below has a known, unambiguous correct answer.
"""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from data_pipeline.ingest.upsert import upsert_metadata, upsert_price_bars
from data_pipeline.screener import screen


def _price_df(n_days: int, close: np.ndarray, volume: np.ndarray, start="2020-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=n_days)
    df = pd.DataFrame(
        {
            "raw_open": close, "raw_high": close * 1.01, "raw_low": close * 0.99,
            "raw_close": close, "raw_volume": volume,
            "adj_open": close, "adj_high": close * 1.01, "adj_low": close * 0.99,
            "adj_close": close, "adj_volume": volume,
            "fetched_at": datetime.now(tz=timezone.utc),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _seed(session, ticker: str, sector: str, n_days: int, close: np.ndarray, volume: np.ndarray, start="2020-01-01") -> None:
    upsert_price_bars(session, ticker, _price_df(n_days, close, volume, start))
    upsert_metadata(session, ticker, {"sector": sector, "industry": None, "listing_status": "active"})
    session.commit()


@pytest.fixture
def liquidity_group(db_session):
    """Three tickers, identical price level, deliberately different volume --
    a known, unambiguous liquidity ranking (HIGH > MED > LOW)."""
    n = 70
    close = np.full(n, 100.0)
    _seed(db_session, "HIGHLIQ", "TestSector", n, close, np.full(n, 5_000_000.0))
    _seed(db_session, "MEDLIQ", "TestSector", n, close, np.full(n, 1_000_000.0))
    _seed(db_session, "LOWLIQ", "TestSector", n, close, np.full(n, 100_000.0))
    return db_session


def test_liquidity_ranking_is_correct(liquidity_group):
    result = screen(liquidity_group, sector="TestSector", metric="liquidity")
    assert result.group_size == 3
    ranked_tickers = [c.ticker for c in result.candidates]
    assert ranked_tickers == ["HIGHLIQ", "MEDLIQ", "LOWLIQ"]
    assert result.candidates[0].percentile == 100.0
    assert result.candidates[-1].percentile == 0.0


def test_sector_filter_excludes_other_sectors(db_session):
    n = 70
    close = np.full(n, 100.0)
    _seed(db_session, "INSECTOR", "SectorA", n, close, np.full(n, 1_000_000.0))
    _seed(db_session, "OUTSECTOR", "SectorB", n, close, np.full(n, 1_000_000.0))

    result = screen(db_session, sector="SectorA", metric="liquidity")

    assert result.group_size == 1
    assert result.candidates[0].ticker == "INSECTOR"


def test_no_matching_sector_returns_empty_result(db_session):
    result = screen(db_session, sector="NotARealSector")
    assert result.group_size == 0
    assert result.candidates == []


def test_ticker_with_too_little_history_is_excluded(db_session):
    """A ticker with fewer than _MIN_OBSERVATIONS price rows must not appear
    in the ranked group at all -- not with a null metric_value, absent
    entirely, matching Component 7's design decision."""
    n_enough = 70
    close_enough = np.full(n_enough, 100.0)
    _seed(db_session, "ENOUGHDATA", "TestSector", n_enough, close_enough, np.full(n_enough, 1_000_000.0))

    n_thin = 3  # below _MIN_OBSERVATIONS = 5
    close_thin = np.full(n_thin, 100.0)
    _seed(db_session, "THINDATA", "TestSector", n_thin, close_thin, np.full(n_thin, 1_000_000.0))

    result = screen(db_session, sector="TestSector", metric="liquidity")

    tickers = {c.ticker for c in result.candidates}
    assert "ENOUGHDATA" in tickers
    assert "THINDATA" not in tickers
    assert result.group_size == 1


@pytest.fixture
def regime_change_ticker(db_session):
    """One ticker: 150 calm business days (tiny daily moves) followed by 100
    volatile ones (large daily moves), at a known split point -- gives every
    as_of test below a known, unambiguous correct answer, unlike relying on
    real historical market volatility actually having behaved a certain way."""
    rng = np.random.default_rng(0)
    calm_returns = rng.normal(0.0, 0.001, 150)
    volatile_returns = rng.normal(0.0, 0.05, 100)
    returns = np.concatenate([calm_returns, volatile_returns])
    close = 100.0 * np.exp(np.cumsum(returns))
    volume = np.full(250, 1_000_000.0)
    _seed(db_session, "REGIMECHANGE", "TestSector", 250, close, volume)
    dates = pd.bdate_range("2020-01-01", periods=250)
    return db_session, dates


def test_as_of_changes_computed_volatility(regime_change_ticker):
    """The core point-in-time claim, made deterministic: screening as_of a
    date deep in the calm period must show LOW volatility; screening as_of a
    date deep in the volatile period (same ticker, same code, only as_of
    differs) must show HIGH volatility. Both as_of dates are chosen so the
    default 63-day lookback window sits entirely within one regime or the
    other, never straddling the split."""
    session, dates = regime_change_ticker
    calm_as_of = dates[140].date()  # 63-day window = days 78-140, entirely calm
    volatile_as_of = dates[240].date()  # 63-day window = days 178-240, entirely volatile

    calm_result = screen(session, sector="TestSector", metric="volatility", as_of=calm_as_of)
    volatile_result = screen(session, sector="TestSector", metric="volatility", as_of=volatile_as_of)

    calm_value = calm_result.candidates[0].metric_value
    volatile_value = volatile_result.candidates[0].metric_value
    assert volatile_value > calm_value * 5, (
        f"volatile-period std ({volatile_value}) should be dramatically higher than "
        f"calm-period std ({calm_value}) for the same ticker -- if these are close, "
        f"as_of isn't actually constraining which rows feed the computation"
    )


def test_as_of_none_uses_latest_available_data(regime_change_ticker):
    """Omitting as_of must use the most recent data (the volatile tail) --
    the same as passing as_of equal to the ticker's last available date."""
    session, dates = regime_change_ticker
    latest = screen(session, sector="TestSector", metric="volatility", as_of=None)
    explicit_last_date = screen(session, sector="TestSector", metric="volatility", as_of=dates[-1].date())
    assert latest.candidates[0].metric_value == explicit_last_date.candidates[0].metric_value
