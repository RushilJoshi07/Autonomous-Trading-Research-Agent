from datetime import datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from data_pipeline.config import settings
from data_pipeline.db.init_db import create_schema
from data_pipeline.db.models import Base


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
