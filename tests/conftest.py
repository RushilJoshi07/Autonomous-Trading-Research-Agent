"""Root-level fixtures shared across all test packages.

Any fixture defined here is visible to tests/data_pipeline/, tests/backtester/,
and any future test package. Package-specific helpers stay in their own conftest.py.
"""
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
