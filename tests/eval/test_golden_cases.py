"""Regression coverage for eval/golden_cases.py -- Stage 6, Component 1.

Promotes Component 1's own scratch verification (a one-off script that
built and cleaned up all six fixtures against the real dev database, by
hand, once) into a permanent, automated check against the TEST database.
This is directly motivated by the real bug Component 2 found: a ticker
name exceeding PriceBar.ticker's String(16) limit was only caught by that
manual script, and a database-level construction failure later crashed a
live run because nothing had exercised "can every builder actually
persist and clean up" as a standing, zero-cost, CI-visible check. This
file is that check, made permanent.

Deliberately does NOT drive any case through the execution loop or
render_verdict -- that is Component 2's own live-only territory (see
test_harness.py's module docstring for why). This file only asks: does
each builder produce a schema-valid GoldenCase, and can it be persisted
and fully torn down.
"""

from eval.fixtures import cleanup, verify_cleanup
from eval.golden_cases import GOLDEN_CASE_BUILDERS, GoldenCase

_VALID_CATEGORIES = {"planted_true", "planted_false", "known_caveat"}
_VALID_STATUSES = {"confirmed", "rejected", "inconclusive"}


def test_every_builder_produces_a_schema_valid_case(eval_db_session):
    for builder in GOLDEN_CASE_BUILDERS:
        case = builder()
        try:
            assert isinstance(case, GoldenCase)
            assert case.category in _VALID_CATEGORIES
            assert case.expected_status in _VALID_STATUSES
            assert case.charter_id and case.hypothesis_id and case.design_id
        finally:
            cleanup(case.ticker, case.charter_id, case.hypothesis_id)


def test_every_builder_cleans_up_completely(eval_db_session):
    """The exact check that would have caught the ticker-length bug and
    the leftover-row bug that later crashed a live run -- build, clean up,
    then verify by direct query rather than trusting the delete calls.
    """
    for builder in GOLDEN_CASE_BUILDERS:
        case = builder()
        cleanup(case.ticker, case.charter_id, case.hypothesis_id)
        ok, detail = verify_cleanup(case.ticker, case.charter_id, case.hypothesis_id)
        assert ok, f"{case.name} did not clean up fully: {detail}"


def test_ticker_names_fit_the_real_price_bar_column(eval_db_session):
    """A direct, named regression test for the exact bug found in
    docs/explanations/stage-6/step-01-golden-cases.md: PriceBar.ticker is
    String(16), and four of the six original ticker names exceeded it.
    Checking the constraint directly here means a future case that
    reintroduces an overlong ticker fails fast, in this test, rather than
    on a real database insert during a live (paid) run.
    """
    for builder in GOLDEN_CASE_BUILDERS:
        case = builder()
        try:
            assert len(case.ticker) <= 16, f"{case.name}'s ticker {case.ticker!r} exceeds PriceBar.ticker's String(16)"
        finally:
            cleanup(case.ticker, case.charter_id, case.hypothesis_id)


def test_each_case_has_a_distinct_ticker(eval_db_session):
    """Golden cases coexist in the database simultaneously during a real
    run_golden_set invocation (each case is cleaned up only after ITS OWN
    run, not before the next case starts) -- a duplicate ticker across two
    cases would make their price data collide.
    """
    tickers = []
    for builder in GOLDEN_CASE_BUILDERS:
        case = builder()
        tickers.append(case.ticker)
        cleanup(case.ticker, case.charter_id, case.hypothesis_id)
    assert len(tickers) == len(set(tickers))


def test_known_caveat_case_declares_the_expected_substring(eval_db_session):
    """golden_caveat_thin_sample is the only case with a non-None
    expected_caveat_substring -- a direct check that Component 1's own
    declared expectation is still present, since eval.harness._score's
    caveats_ok logic depends on it.
    """
    caveat_cases = [b() for b in GOLDEN_CASE_BUILDERS]
    try:
        known_caveat = [c for c in caveat_cases if c.category == "known_caveat"]
        assert len(known_caveat) == 1
        assert known_caveat[0].expected_caveat_substring is not None
        others = [c for c in caveat_cases if c.category != "known_caveat"]
        assert all(c.expected_caveat_substring is None for c in others)
    finally:
        for c in caveat_cases:
            cleanup(c.ticker, c.charter_id, c.hypothesis_id)
