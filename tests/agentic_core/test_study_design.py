"""Regression coverage for agentic_core/study_design.py -- Stage 5,
Component 5 formal coverage.

Covers only the deterministic pieces: real DB-backed date-bound queries and
pure date-window arithmetic. propose_study_design's LLM call itself is
exercised live, not mocked, the same way Component 2/4's charter and
hypothesis prompts were -- see docs/explanations/stage-5/step-06-study-design.md
for that verification.
"""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from agentic_core.schemas import DateRange, ParsedStudyDesign, StudyDesign
from agentic_core.study_design import (
    InsufficientHistoryError,
    _common_price_bounds,
    _fold_boundaries,
    _simple_holdout,
    _trading_dates,
    _walk_forward_windows,
)
from data_pipeline.db.models import PriceBar


def _insert_bars(session, ticker: str, dates: list[date]) -> None:
    for i, d in enumerate(dates):
        price = 100.0 + i * 0.01
        session.add(
            PriceBar(
                ticker=ticker,
                date=d,
                raw_open=price, raw_high=price, raw_low=price, raw_close=price, raw_volume=1_000_000,
                adj_open=price, adj_high=price, adj_low=price, adj_close=price, adj_volume=1_000_000,
                fetched_at=datetime.now(tz=timezone.utc),
            )
        )
    session.commit()


@pytest.fixture
def two_ticker_bounds(db_session):
    """AAA has data 2020-01-01..2021-06-30; BBB starts later and ends
    earlier -- the overlap (the intersection _common_price_bounds must
    return) is a strict subset of AAA's own range on both ends.
    """
    aaa_dates = [d.date() for d in pd.bdate_range("2020-01-01", "2021-06-30")]
    bbb_dates = [d.date() for d in pd.bdate_range("2020-03-01", "2021-04-30")]
    _insert_bars(db_session, "AAA", aaa_dates)
    _insert_bars(db_session, "BBB", bbb_dates)
    return db_session, aaa_dates, bbb_dates


def test_common_price_bounds_is_the_intersection_not_the_union(two_ticker_bounds):
    session, _, bbb_dates = two_ticker_bounds
    earliest, latest = _common_price_bounds(session, ["AAA", "BBB"], history_start=None)
    assert (earliest, latest) == (bbb_dates[0], bbb_dates[-1])


def test_common_price_bounds_respects_history_start_floor(two_ticker_bounds):
    session, _, bbb_dates = two_ticker_bounds
    floor = date(2020, 6, 1)
    earliest, latest = _common_price_bounds(session, ["AAA", "BBB"], history_start=floor)
    assert earliest == floor
    assert latest == bbb_dates[-1]


def test_common_price_bounds_raises_for_missing_ticker(two_ticker_bounds):
    session, _, _ = two_ticker_bounds
    with pytest.raises(InsufficientHistoryError, match="CCC"):
        _common_price_bounds(session, ["AAA", "CCC"], history_start=None)


def test_common_price_bounds_raises_when_no_overlap(db_session):
    _insert_bars(db_session, "AAA", [d.date() for d in pd.bdate_range("2020-01-01", "2020-06-30")])
    _insert_bars(db_session, "BBB", [d.date() for d in pd.bdate_range("2021-01-01", "2021-06-30")])
    with pytest.raises(InsufficientHistoryError):
        _common_price_bounds(db_session, ["AAA", "BBB"], history_start=None)


def test_trading_dates_returns_sorted_dates_in_range(two_ticker_bounds):
    session, aaa_dates, _ = two_ticker_bounds
    start, end = aaa_dates[10], aaa_dates[20]
    result = _trading_dates(session, "AAA", start, end)
    assert result == aaa_dates[10:21]
    assert result == sorted(result)


# ---- pure date-window arithmetic (no DB) ----

_DATES = [d.date() for d in pd.bdate_range("2020-01-01", periods=1000)]


def test_simple_holdout_splits_at_the_right_fraction_with_no_gap():
    in_sample, out_of_sample = _simple_holdout(_DATES, 0.7)
    assert in_sample.start == _DATES[0]
    assert out_of_sample.end == _DATES[-1]
    # no gap, no overlap: out_of_sample starts the trading day right after in_sample ends
    assert _DATES.index(out_of_sample.start) == _DATES.index(in_sample.end) + 1
    n_in = _DATES.index(in_sample.end) - _DATES.index(in_sample.start) + 1
    assert n_in == round(len(_DATES) * 0.7)


def test_simple_holdout_raises_below_minimum_window():
    with pytest.raises(InsufficientHistoryError):
        _simple_holdout(_DATES[:10], 0.7)


def test_fold_boundaries_partition_with_no_gaps_or_overlaps():
    folds = _fold_boundaries(_DATES, 4)
    assert len(folds) == 4
    covered = []
    for f in folds:
        covered.extend(_DATES[_DATES.index(f.start):_DATES.index(f.end) + 1])
    assert covered == _DATES
    for a, b in zip(folds, folds[1:]):
        assert _DATES.index(b.start) == _DATES.index(a.end) + 1


def test_fold_boundaries_raises_when_a_fold_would_be_too_short():
    with pytest.raises(InsufficientHistoryError):
        _fold_boundaries(_DATES[:30], 5)


def test_walk_forward_windows_first_window_is_the_holdout_in_sample():
    windows = _walk_forward_windows(_DATES, 0.7, 3)
    assert len(windows) == 4  # in_sample + 3 oos folds
    in_sample, out_of_sample = _simple_holdout(_DATES, 0.7)
    assert windows[0] == in_sample
    assert windows[1].start == out_of_sample.start
    assert windows[-1].end == _DATES[-1]
    # the oos folds partition exactly the remainder after in_sample, no gaps/overlaps
    covered = []
    for f in windows[1:]:
        covered.extend(_DATES[_DATES.index(f.start):_DATES.index(f.end) + 1])
    assert covered == _DATES[_DATES.index(windows[0].end) + 1:]


# ---- schema validators (agentic_core/schemas.py) ----


def test_date_range_rejects_start_after_end():
    with pytest.raises(ValueError):
        DateRange(start=date(2024, 1, 5), end=date(2024, 1, 1))


def test_parsed_study_design_rejects_walk_forward_without_folds():
    with pytest.raises(ValueError):
        ParsedStudyDesign(design_type="walk_forward", split="70/30", rationale="x")


def test_parsed_study_design_rejects_simple_holdout_with_folds():
    with pytest.raises(ValueError):
        ParsedStudyDesign(design_type="simple_holdout", split="70/30", walk_forward_folds=3, rationale="x")


def test_study_design_rejects_out_of_sample_before_in_sample_ends():
    parsed = ParsedStudyDesign(design_type="simple_holdout", split="70/30", rationale="x")
    with pytest.raises(ValueError):
        StudyDesign(
            parsed=parsed,
            in_sample=DateRange(start=date(2020, 1, 1), end=date(2021, 1, 1)),
            out_of_sample=DateRange(start=date(2020, 6, 1), end=date(2022, 1, 1)),
            null_hypothesis="x",
        )
