import csv
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from data_pipeline.db.models import PriceBar
from data_pipeline.fetch.prices import fetch_prices
from data_pipeline.ingest.upsert import upsert_price_bars

FIXTURES = Path(__file__).parent / "fixtures" / "known_prices.csv"
TOLERANCE = 0.01  # $0.01 — detects wrong data, not float formatting


def test_data_matches_known_source(db_session):
    """Price stored in the DB matches a hand-verified value from an independent source.

    This is the only test that calls real yfinance. It is slow (~3s) by design:
    the point is to confirm the pipeline is not corrupting data, not to be fast.
    """
    with open(FIXTURES) as f:
        reader = csv.DictReader(f)
        golden = {
            (row["ticker"], row["date"]): float(row["raw_close"])
            for row in reader
        }

    for (ticker, date_str), expected_close in golden.items():
        target_date = date.fromisoformat(date_str)

        df = fetch_prices(ticker, start=target_date, end=target_date + timedelta(days=1))
        assert not df.empty, f"fetch_prices returned no data for {ticker} on {date_str}"

        upsert_price_bars(db_session, ticker, df)
        db_session.commit()

        bar = db_session.execute(
            select(PriceBar).where(
                PriceBar.ticker == ticker,
                PriceBar.date == target_date,
            )
        ).scalar_one_or_none()

        assert bar is not None, f"No row found in DB for {ticker} on {date_str}"
        actual = float(bar.raw_close)
        assert abs(actual - expected_close) < TOLERANCE, (
            f"{ticker} {date_str}: DB has {actual}, fixture says {expected_close} "
            f"(tolerance ${TOLERANCE}). Check yfinance or the fixture source."
        )
