"""data_pipeline-specific test helpers.

DB fixtures (test_engine, db_session, patch_runner_session_factory) are defined
in tests/conftest.py and inherited automatically by pytest.
"""
from datetime import datetime, timezone

import pandas as pd


def make_price_df(start_date, n_days=3):
    """Return a fake price DataFrame in the shape fetch_prices returns."""
    dates = pd.date_range(start=start_date, periods=n_days, freq="B")
    df = pd.DataFrame(
        {
            "raw_open": 100.0, "raw_high": 105.0, "raw_low": 99.0,
            "raw_close": 102.0, "raw_volume": 1_000_000,
            "adj_open": 100.0, "adj_high": 105.0, "adj_low": 99.0,
            "adj_close": 102.0, "adj_volume": 1_000_000,
            "fetched_at": datetime.now(tz=timezone.utc),
        },
        index=dates,
    )
    df.index.name = "date"
    return df
