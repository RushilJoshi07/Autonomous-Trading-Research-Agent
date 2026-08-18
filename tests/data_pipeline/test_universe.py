"""Tests for universe.py -- the live-query replacement for Stage 1's static
ticker list.

Component 8 (Stage 4) formal coverage for Component 7.
"""

from data_pipeline.ingest.upsert import upsert_metadata
from data_pipeline.universe import all_tickers


def test_all_tickers_returns_sorted_ingested_tickers(db_session):
    upsert_metadata(db_session, "ZEBRA", {"sector": "Test", "industry": None, "listing_status": "active"})
    upsert_metadata(db_session, "APPLE", {"sector": "Test", "industry": None, "listing_status": "active"})
    db_session.commit()

    result = all_tickers(db_session)

    assert result == ["APPLE", "ZEBRA"]


def test_all_tickers_empty_database_returns_empty_list(db_session):
    assert all_tickers(db_session) == []
