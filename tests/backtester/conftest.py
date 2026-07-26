"""Shared fixtures for backtester tests.

Reuses the test_engine and db_session fixtures from tests/data_pipeline/conftest.py
via pytest's conftest inheritance (pytest looks up the fixture chain automatically).

Also provides make_synthetic_data — a deterministic price series used in gate tests.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from data_pipeline.ingest.upsert import upsert_price_bars


def make_synthetic_data(n_bars: int = 500, seed: int = 42) -> pd.DataFrame:
    """Return a realistic-looking OHLCV DataFrame with a DatetimeIndex.

    Uses a seeded random walk so results are deterministic across runs.
    Returned shape matches both what upsert_price_bars expects (adj_* columns,
    index named 'date') AND what backtesting.py expects (Open/High/Low/Close/Volume,
    index named 'Date') — callers pick the right column names for their context.
    """
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
            "Volume": (rng.integers(500_000, 2_000_000, n_bars)).astype(float),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def synthetic_to_db_df(bt_df: pd.DataFrame) -> pd.DataFrame:
    """Convert a backtesting.py-shaped DataFrame to the shape upsert_price_bars expects."""
    db_df = pd.DataFrame(
        {
            "raw_open":   bt_df["Open"],
            "raw_high":   bt_df["High"],
            "raw_low":    bt_df["Low"],
            "raw_close":  bt_df["Close"],
            "raw_volume": bt_df["Volume"].astype(int),
            "adj_open":   bt_df["Open"],
            "adj_high":   bt_df["High"],
            "adj_low":    bt_df["Low"],
            "adj_close":  bt_df["Close"],
            "adj_volume": bt_df["Volume"].astype(int),
            "fetched_at": datetime.now(tz=timezone.utc),
        },
        index=bt_df.index,
    )
    db_df.index.name = "date"
    return db_df


@pytest.fixture
def synthetic_data():
    """The canonical 500-bar synthetic dataset used across gate tests."""
    return make_synthetic_data()


@pytest.fixture
def seeded_db(db_session, synthetic_data):
    """Seed the test DB with synthetic AAPL-like price data and return the session."""
    db_df = synthetic_to_db_df(synthetic_data)
    upsert_price_bars(db_session, "SYNTHETIC", db_df)
    db_session.commit()
    return db_session
