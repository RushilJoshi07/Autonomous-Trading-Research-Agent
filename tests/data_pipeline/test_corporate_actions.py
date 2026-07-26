"""Tests for corporate-action detection and adjusted-price re-fetch.

Uses AAPL's real 4-for-1 split on 2020-08-31 as fixture data.
We simulate a cache that was populated BEFORE the split was known, then
run check_corporate_actions and verify the split is detected and adj_*
prices are rewritten while raw_* prices stay unchanged.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import select

from data_pipeline.db.models import CorporateActionLog, PriceBar
from data_pipeline.ingest.corporate_actions import handle_corporate_actions
from data_pipeline.ingest.upsert import upsert_price_bars

_TICKER = "AAPL"
_SPLIT_DATE = date(2020, 8, 31)

# Prices seeded into the cache as if the split had NOT yet occurred.
# Before the 4:1 split, AAPL traded around $400-500.
# We use round numbers so the post-split adj_* (÷4) is easy to verify.
_PRE_SPLIT_DATES = [date(2020, 8, 28), date(2020, 8, 31)]
_SEEDED_RAW_CLOSE = 400.0
_SEEDED_ADJ_CLOSE_BEFORE_SPLIT = 400.0  # "stale" — doesn't yet reflect the split

# After the split the adjusted close should be ~100.0 (400 / 4).
_EXPECTED_ADJ_CLOSE_AFTER_SPLIT = pytest.approx(100.0, abs=5.0)


def _make_pre_split_df():
    """DataFrame that looks like what was fetched before anyone knew about the split."""
    dates = pd.DatetimeIndex([pd.Timestamp(d) for d in _PRE_SPLIT_DATES])
    df = pd.DataFrame(
        {
            "raw_open": _SEEDED_RAW_CLOSE,
            "raw_high": _SEEDED_RAW_CLOSE + 10,
            "raw_low": _SEEDED_RAW_CLOSE - 10,
            "raw_close": _SEEDED_RAW_CLOSE,
            "raw_volume": 50_000_000,
            "adj_open": _SEEDED_ADJ_CLOSE_BEFORE_SPLIT,
            "adj_high": _SEEDED_ADJ_CLOSE_BEFORE_SPLIT + 10,
            "adj_low": _SEEDED_ADJ_CLOSE_BEFORE_SPLIT - 10,
            "adj_close": _SEEDED_ADJ_CLOSE_BEFORE_SPLIT,
            "adj_volume": 50_000_000,
            "fetched_at": datetime.now(tz=timezone.utc),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _make_post_split_adjusted_df():
    """DataFrame that yfinance returns AFTER it knows about the split.

    adj_* prices are divided by 4; raw_* are unchanged.
    """
    dates = pd.DatetimeIndex([pd.Timestamp(d) for d in _PRE_SPLIT_DATES])
    adj = _SEEDED_ADJ_CLOSE_BEFORE_SPLIT / 4.0
    df = pd.DataFrame(
        {
            "raw_open": _SEEDED_RAW_CLOSE,
            "raw_high": _SEEDED_RAW_CLOSE + 10,
            "raw_low": _SEEDED_RAW_CLOSE - 10,
            "raw_close": _SEEDED_RAW_CLOSE,
            "raw_volume": 50_000_000,
            "adj_open": adj,
            "adj_high": adj + 2.5,
            "adj_low": adj - 2.5,
            "adj_close": adj,
            "adj_volume": 200_000_000,  # post-split volume in adjusted shares
            "fetched_at": datetime.now(tz=timezone.utc),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def test_split_detection_and_readjustment(db_session):
    """Detect a new split; rewrite adj_* but leave raw_* untouched.

    Steps:
    1. Seed the DB with pre-split price rows (adj_close = 400, raw_close = 400).
    2. Mock fetch_corporate_actions to return the real 2020-08-31 4:1 split.
    3. Mock fetch_prices to return post-split adjusted data (adj_close ≈ 100).
    4. Run handle_corporate_actions(["AAPL"]).

    Assertions:
    - One CorporateActionLog row for the split.
    - adj_close is now ≈ 100 (updated).
    - raw_close is still 400 (unchanged — the load-bearing claim).
    """
    # Step 1 — seed stale pre-split data
    upsert_price_bars(db_session, _TICKER, _make_pre_split_df())
    db_session.commit()

    # Confirm raw_close was seeded correctly
    bars_before = db_session.execute(
        select(PriceBar).where(PriceBar.ticker == _TICKER)
    ).scalars().all()
    assert len(bars_before) == len(_PRE_SPLIT_DATES)
    for bar in bars_before:
        assert float(bar.raw_close) == pytest.approx(_SEEDED_RAW_CLOSE, abs=0.01)

    # Step 2-4 — run the handler with mocked external calls
    fake_action = {
        "action_type": "split",
        "action_date": _SPLIT_DATE,
        "value": Decimal("4.0"),
    }

    with patch(
        "data_pipeline.ingest.corporate_actions.fetch_corporate_actions",
        return_value=[fake_action],
    ), patch(
        "data_pipeline.ingest.corporate_actions.fetch_prices",
        return_value=_make_post_split_adjusted_df(),
    ):
        handle_corporate_actions(db_session, _TICKER)
        db_session.commit()

    # Assert: one CorporateActionLog row landed
    action_rows = db_session.execute(
        select(CorporateActionLog).where(CorporateActionLog.ticker == _TICKER)
    ).scalars().all()
    assert len(action_rows) == 1
    action = action_rows[0]
    assert action.action_type == "split"
    assert action.action_date == _SPLIT_DATE
    assert float(action.value) == pytest.approx(4.0, abs=0.001)

    # Assert: adj_close updated; raw_close untouched
    bars_after = db_session.execute(
        select(PriceBar).where(PriceBar.ticker == _TICKER)
    ).scalars().all()
    assert len(bars_after) == len(_PRE_SPLIT_DATES)

    for bar in bars_after:
        # raw_close must be byte-for-byte the original value
        assert float(bar.raw_close) == pytest.approx(_SEEDED_RAW_CLOSE, abs=0.01), (
            f"raw_close changed on {bar.date} — the raw/adj separation failed"
        )
        # adj_close must reflect the ÷4 adjustment
        assert float(bar.adj_close) == _EXPECTED_ADJ_CLOSE_AFTER_SPLIT, (
            f"adj_close not updated on {bar.date}: got {bar.adj_close}"
        )
