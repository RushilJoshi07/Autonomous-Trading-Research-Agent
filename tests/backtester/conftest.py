"""Shared fixtures for backtester tests.

Reuses the test_engine and db_session fixtures from tests/data_pipeline/conftest.py,
and make_synthetic_data/synthetic_data from the root tests/conftest.py, via pytest's
conftest inheritance (pytest looks up the fixture chain automatically).
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from data_pipeline.ingest.upsert import upsert_price_bars


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
def seeded_db(db_session, synthetic_data):
    """Seed the test DB with synthetic AAPL-like price data and return the session."""
    db_df = synthetic_to_db_df(synthetic_data)
    upsert_price_bars(db_session, "SYNTHETIC", db_df)
    db_session.commit()
    return db_session
