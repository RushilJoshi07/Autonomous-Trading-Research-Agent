"""Coverage for agentic_core/charter.py -- Stage 7 Component 2's own domain
logic (create_charter/confirm_charter already existed from Stage 5;
correct_charter and confirm_charter's blocked-guard are new here).

parse_charter and resolve_universe are monkeypatched with deterministic
fakes throughout -- no real LLM call, no real screener query. The fake
parse_charter deliberately behaves like a plausible stand-in LLM (it reads
the text it's given and changes its answer) rather than returning a fixed
value regardless of input, specifically so the correction tests can prove
_combined_mandate_for_correction's output actually reaches parse_charter
and actually changes what comes back -- not just that a second row gets
inserted.
"""

from datetime import date

import pytest

from agentic_core.charter import (
    CharterAlreadyConfirmedError,
    CharterBlockedError,
    CharterNotFoundError,
    CorrectionLimitExceededError,
    MAX_CORRECTION_ROUNDS,
    confirm_charter,
    correct_charter,
    create_charter,
)
from agentic_core.db.models import Charter as CharterRow
from agentic_core.schemas import Charter, EffectFamily, ParsedCharter, UniverseFilter


def _fake_parse_charter(mandate_text: str) -> ParsedCharter:
    """Starts narrow (Technology/Consumer Electronics); widens to
    sector-only the moment the text it's given mentions 'all of tech' --
    simulating a correction actually being read and acted on.
    """
    if "all of tech" in mandate_text:
        return ParsedCharter(
            universe=UniverseFilter(sector="Technology", industry=None),
            hypothesis_families=[EffectFamily.LOW_VOLATILITY],
        )
    return ParsedCharter(
        universe=UniverseFilter(sector="Technology", industry="Consumer Electronics"),
        hypothesis_families=[EffectFamily.LOW_VOLATILITY],
    )


def _fake_resolve_universe(parsed: ParsedCharter, as_of=None) -> Charter:
    tickers = ["AAPL"] if parsed.universe.industry else ["AAPL", "MSFT", "GOOGL"]
    return Charter(parsed=parsed, resolved_universe=tickers, screening_as_of=date(2026, 1, 1), screening_group_size=40)


def _fake_resolve_universe_empty(parsed: ParsedCharter, as_of=None) -> Charter:
    return Charter(parsed=parsed, resolved_universe=[], screening_as_of=date(2026, 1, 1), screening_group_size=0)


@pytest.fixture(autouse=True)
def _patch_llm_and_screener(monkeypatch):
    monkeypatch.setattr("agentic_core.charter.parse_charter", _fake_parse_charter)
    monkeypatch.setattr("agentic_core.charter.resolve_universe", _fake_resolve_universe)


def test_create_charter_persists_round_zero(charter_db_session):
    charter_id, charter, blocked = create_charter("Investigate consumer tech companies.")

    assert blocked is False
    row = charter_db_session.get(CharterRow, charter_id)
    assert row.mandate_text == "Investigate consumer tech companies."
    assert row.correction_round == 0
    assert row.parent_charter_id is None
    assert row.correction_text is None
    assert row.confirmed is False
    assert charter.resolved_universe == ["AAPL"]


def test_correct_charter_creates_new_chained_row(charter_db_session):
    original_id, _, _ = create_charter("Investigate consumer tech companies.")

    corrected_id, corrected, blocked = correct_charter(original_id, "that's too narrow, include all of tech")

    assert corrected_id != original_id
    row = charter_db_session.get(CharterRow, corrected_id)
    assert row.parent_charter_id == original_id
    assert row.correction_round == 1
    assert row.correction_text == "that's too narrow, include all of tech"
    # Denormalized root mandate_text, not the combined re-parse prompt.
    assert row.mandate_text == "Investigate consumer tech companies."
    # The original row is untouched -- immutability, not a mutation in place.
    original_row = charter_db_session.get(CharterRow, original_id)
    assert original_row.correction_round == 0
    assert original_row.charter["resolved_universe"] == ["AAPL"]


def test_correction_actually_changes_the_interpretation(charter_db_session):
    """Proves the combined prompt (original + restated interpretation +
    correction) really reaches parse_charter, not just that a row gets
    inserted -- the fake only widens the universe when it sees the phrase
    'all of tech' in the text it's handed.
    """
    original_id, original, _ = create_charter("Investigate consumer tech companies.")
    assert original.parsed.universe.industry == "Consumer Electronics"

    _, corrected, _ = correct_charter(original_id, "that's too narrow, include all of tech")

    assert corrected.parsed.universe.industry is None
    assert corrected.resolved_universe == ["AAPL", "MSFT", "GOOGL"]


def test_two_correction_rounds_then_limit_exceeded(charter_db_session):
    round0_id, _, _ = create_charter("Investigate consumer tech companies.")
    round1_id, _, _ = correct_charter(round0_id, "widen it")
    round2_id, _, _ = correct_charter(round1_id, "widen it again")

    round2_row = charter_db_session.get(CharterRow, round2_id)
    assert round2_row.correction_round == MAX_CORRECTION_ROUNDS == 2

    with pytest.raises(CorrectionLimitExceededError):
        correct_charter(round2_id, "one more please")


def test_correct_charter_on_confirmed_charter_raises(charter_db_session):
    charter_id, _, _ = create_charter("Investigate consumer tech companies.")
    confirm_charter(charter_id)

    with pytest.raises(CharterAlreadyConfirmedError):
        correct_charter(charter_id, "actually, widen it")


def test_correct_charter_not_found_raises(charter_db_session):
    with pytest.raises(CharterNotFoundError):
        correct_charter("does-not-exist", "widen it")


def test_confirm_charter_happy_path(charter_db_session):
    charter_id, _, _ = create_charter("Investigate consumer tech companies.")

    confirm_charter(charter_id)

    row = charter_db_session.get(CharterRow, charter_id)
    assert row.confirmed is True
    assert row.confirmed_at is not None


def test_confirm_charter_not_found_raises(charter_db_session):
    with pytest.raises(CharterNotFoundError):
        confirm_charter("does-not-exist")


def test_confirm_charter_blocked_raises(charter_db_session, monkeypatch):
    monkeypatch.setattr("agentic_core.charter.resolve_universe", _fake_resolve_universe_empty)
    charter_id, charter, blocked = create_charter("Investigate a universe that matches nothing.")
    assert blocked is True

    with pytest.raises(CharterBlockedError):
        confirm_charter(charter_id)

    row = charter_db_session.get(CharterRow, charter_id)
    assert row.confirmed is False


def test_confirm_charter_is_idempotent(charter_db_session):
    charter_id, _, _ = create_charter("Investigate consumer tech companies.")

    confirm_charter(charter_id)
    first_confirmed_at = charter_db_session.get(CharterRow, charter_id).confirmed_at
    confirm_charter(charter_id)
    second_confirmed_at = charter_db_session.get(CharterRow, charter_id).confirmed_at

    assert second_confirmed_at >= first_confirmed_at
