"""Root-level fixtures shared across all test packages.

Any fixture defined here is visible to tests/data_pipeline/, tests/backtester/,
tests/research_stats/, and any future test package. Package-specific helpers
stay in their own conftest.py.
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import pytest

from data_pipeline.config import settings
from data_pipeline.db.init_db import create_schema


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(settings.database_url_test)
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine)


@pytest.fixture
def db_session(test_engine):
    """Clean session per test. Truncates all tables before yielding."""
    with test_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE ingestion_run_tickers, ingestion_runs, "
            "price_bars, ticker_metadata, corporate_actions_log CASCADE"
        ))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def patch_runner_session_factory(test_session_factory, monkeypatch):
    """Point the runner's SessionFactory at the test database for every test."""
    monkeypatch.setattr("data_pipeline.ingest.runner.SessionFactory", test_session_factory)


def make_synthetic_data(n_bars: int = 500, seed: int = 42) -> pd.DataFrame:
    """Return a realistic-looking OHLCV DataFrame with a DatetimeIndex.

    Uses a seeded random walk so results are deterministic across runs.
    Returned shape matches both what upsert_price_bars expects (adj_* columns,
    index named 'date') AND what backtesting.py expects (Open/High/Low/Close/Volume,
    index named 'Date') — callers pick the right column names for their context.

    Lives here (root conftest), not tests/backtester/conftest.py, because
    tests/research_stats/ needs it too — pytest's conftest inheritance only
    flows downward (a directory's conftest is visible to itself and its
    subdirectories, not to sibling directories), so a fixture two sibling
    packages both need has to live at their common ancestor.
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


@pytest.fixture
def synthetic_data():
    """The canonical 500-bar synthetic dataset used across gate tests."""
    return make_synthetic_data()
