from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from data_pipeline.fetch.client import FetchError
from data_pipeline.db.models import IngestionRun, IngestionRunTicker, PriceBar
from data_pipeline.ingest.runner import ingest_daily

from tests.data_pipeline.conftest import make_price_df

_TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE"]


def test_caching(db_session):
    """Second ingest starts from last_cached_date + 1 day, not from scratch.

    We capture the start argument on both calls. The second call's start must
    be strictly later than the first call's start, proving the runner read the
    DB cache rather than re-fetching from 2010.
    """
    tickers = ["AAA"]
    calls: list[tuple] = []

    def _capture_fetch(ticker, start, end=None):
        calls.append((ticker, start))
        return make_price_df(start_date=start, n_days=2)

    with patch("data_pipeline.ingest.runner.fetch_prices", side_effect=_capture_fetch), \
         patch("data_pipeline.ingest.runner.fetch_metadata", return_value={}):
        ingest_daily(tickers)
        ingest_daily(tickers)

    assert len(calls) == 2, "Expected exactly two fetch_prices calls (one per run)"
    first_start = calls[0][1]
    second_start = calls[1][1]

    assert second_start > first_start, (
        f"Second run started at {second_start}, same or earlier than first run "
        f"({first_start}). The incremental-start caching logic is not working."
    )


def test_partial_run_failure(db_session):
    """A mid-list failure isolates to that ticker; others commit successfully.

    Tickers 0-2 and 4 succeed; ticker 3 (DDD) raises FetchError.
    Expected:
      - price_bars has rows for AAA, BBB, CCC, EEE
      - price_bars has ZERO rows for DDD (no partial write)
      - ingestion_run_tickers has a 'failed' row for DDD with an error message
      - ingestion_runs.status == 'partial_success'
    """
    def _side_effect(ticker, start, end=None):
        if ticker == "DDD":
            raise FetchError("simulated network timeout")
        return make_price_df(start_date=start, n_days=2)

    with patch("data_pipeline.ingest.runner.fetch_prices", side_effect=_side_effect), \
         patch("data_pipeline.ingest.runner.fetch_metadata", return_value={}):
        run_id = ingest_daily(_TICKERS)

    # Overall run status must reflect the single failure
    run = db_session.get(IngestionRun, run_id)
    assert run.status == "partial_success"

    # DDD must have zero rows — the whole-ticker transaction rolled back
    ddd_count = db_session.execute(
        select(func.count()).where(PriceBar.ticker == "DDD")
    ).scalar_one()
    assert ddd_count == 0, "DDD had a partial write — per-ticker isolation failed"

    # DDD must have a failed ingestion_run_tickers row with an error recorded
    ddd_run_row = db_session.execute(
        select(IngestionRunTicker).where(
            IngestionRunTicker.run_id == run_id,
            IngestionRunTicker.ticker == "DDD",
        )
    ).scalar_one()
    assert ddd_run_row.status == "failed"
    assert ddd_run_row.error is not None

    # The other four tickers must have committed rows
    for ticker in ("AAA", "BBB", "CCC", "EEE"):
        count = db_session.execute(
            select(func.count()).where(PriceBar.ticker == ticker)
        ).scalar_one()
        assert count > 0, f"{ticker} has no rows — successful tickers were rolled back"

        ticker_row = db_session.execute(
            select(IngestionRunTicker).where(
                IngestionRunTicker.run_id == run_id,
                IngestionRunTicker.ticker == ticker,
            )
        ).scalar_one()
        assert ticker_row.status == "success"
