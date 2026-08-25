"""Regression coverage for agentic_core/verdict.py -- Stage 5, Component 7.

This is Sacred Gate 2's test file. Two claims are under test, and they need
different kinds of evidence:

  FABRICATION  -- every quantitative claim resolves to a real trace, and a
                  number in the prose with no claim behind it fails the
                  verdict. Tested by trying to fabricate, five ways.

  KILLING      -- the status is decided by code reading real numbers, and
                  the scope that code reads is itself load-bearing. Tested
                  against THE REAL walk-forward evidence from this
                  project's own database, including the deliberate
                  narrowing that would flip it.

The single most important test in this file is
test_narrowing_gate1_to_the_final_window_flips_the_real_study, which
encodes the exact trap: the same pre-registered condition on the same real
data yields "rejected" scored across every out-of-sample window and
"confirmed" scored on the last one alone.
"""

from datetime import date, datetime, timezone

import pytest

from agentic_core.schemas import Claim, FalsificationCondition
from agentic_core.verdict import (
    MIN_TRADES_FOR_CONFIRMATION,
    TIER_SEARCH_BURDEN,
    GateResult,
    WindowEvaluation,
    corrected_threshold,
    decide_status,
    mandatory_caveats,
    scan_for_unreferenced_numbers,
    validate_claims,
)

# ---------------------------------------------------------------------------
# THE REAL EVIDENCE
# ---------------------------------------------------------------------------
# Copied verbatim from this project's own database -- the walk-forward run
# of the LowVol_AAPL_ATR_MeanReversion hypothesis (study design
# a27b937b-8ab0-4b9e-abc1-0172b0dffb71), executed live against real cached
# AAPL data on 2026-08-23. Not synthetic, not illustrative: these are the
# numbers the loop actually recorded.
#
# Window 0 is in-sample. Windows 1-3 are the walk-forward out-of-sample
# folds. Note window 3's +0.941 -- the "deceptively decent ending" that
# makes the scope decision matter.
_REAL_WALK_FORWARD = [
    WindowEvaluation(window_index=0, is_out_of_sample=False, metric_value=0.771,
                     num_trades=73, p_value=0.432, backtest_trace_id=101, significance_trace_id=102),
    WindowEvaluation(window_index=1, is_out_of_sample=True, metric_value=-1.510,
                     num_trades=4, p_value=1.000, backtest_trace_id=103, significance_trace_id=104),
    WindowEvaluation(window_index=2, is_out_of_sample=True, metric_value=0.545,
                     num_trades=6, p_value=0.794, backtest_trace_id=105, significance_trace_id=106),
    WindowEvaluation(window_index=3, is_out_of_sample=True, metric_value=0.941,
                     num_trades=7, p_value=0.312, backtest_trace_id=107, significance_trace_id=108),
]

# The hypothesis's own pre-registered bar, exactly as stored.
_REAL_CONDITION = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.5)

# grounding_tier was 'whitelist_search' and one hypothesis exists under the
# charter, so the real corrected threshold is 0.05 / (1 x 2.0) = 0.025.
_REAL_THRESHOLD = corrected_threshold(1, "whitelist_search")


class _FakeTrace:
    def __init__(self, id, tool_name, result, is_error=False):
        self.id = id
        self.tool_name = tool_name
        self.result = result
        self.is_error = is_error


def _real_traces():
    out = []
    for e in _REAL_WALK_FORWARD:
        out.append(_FakeTrace(e.backtest_trace_id, "run_backtest",
                              {"sharpe_ratio": e.metric_value, "num_trades": e.num_trades}))
        out.append(_FakeTrace(e.significance_trace_id, "test_significance",
                              {"p_value": e.p_value, "observed_sharpe": e.metric_value}))
    return out


# ---------------------------------------------------------------------------
# THE SCOPE TRAP -- the most important test in this component
# ---------------------------------------------------------------------------


def test_real_walk_forward_study_is_rejected():
    """The real study, scored the way the design says to score it."""
    status, gates = decide_status(_REAL_WALK_FORWARD, _REAL_CONDITION, _REAL_THRESHOLD)
    assert status == "rejected"

    by_name = {g.name: g for g in gates}
    assert by_name["pre_registered_falsification"].windows_failing == [1]
    assert by_name["mandatory_control"].windows_failing == [1, 2, 3]


def test_narrowing_gate1_to_the_final_window_flips_the_real_study():
    """THE SAFEGUARD.

    "Mechanical" does not automatically mean "honest" -- a human still
    chooses WHICH DATA the mechanical rule reads. This test encodes the
    exact trap, so that a future narrowing of scope cannot pass unnoticed.

    Scored on the final out-of-sample window alone, the real study's
    sharpe of +0.941 does NOT breach the pre-registered 0.5 bar, and the
    falsification gate would PASS -- the opposite conclusion from the same
    condition on the same data.

    If someone later "simplifies" decide_status to look at the last window,
    test_real_walk_forward_study_is_rejected above fails. This test proves
    that failure would be a real behavioral flip and not a technicality, by
    demonstrating the flipped result directly.
    """
    final_only = [_REAL_WALK_FORWARD[-1]]
    _, gates_narrow = decide_status(final_only, _REAL_CONDITION, _REAL_THRESHOLD)
    _, gates_full = decide_status(_REAL_WALK_FORWARD, _REAL_CONDITION, _REAL_THRESHOLD)

    narrow = {g.name: g for g in gates_narrow}["pre_registered_falsification"]
    full = {g.name: g for g in gates_full}["pre_registered_falsification"]

    assert full.passed is False, "scored across every out-of-sample window, the bar is breached"
    assert narrow.passed is True, (
        "scored on the final window alone, sharpe=+0.941 does NOT breach the pre-registered "
        "0.5 bar -- the same condition on the same data, silently disarmed by scope"
    )

    # What the real study actually shows, stated accurately rather than as
    # predicted: narrowing the scope disarms the FALSIFICATION gate, but the
    # verdict survives anyway because the mandatory control independently
    # rejects window 3 (p=0.312, threshold 0.025). That is defence in depth
    # working -- NOT evidence that scope is harmless. See the next test for
    # a case where scope alone decides the outcome.
    status_full, _ = decide_status(_REAL_WALK_FORWARD, _REAL_CONDITION, _REAL_THRESHOLD)
    status_narrow, _ = decide_status(final_only, _REAL_CONDITION, _REAL_THRESHOLD)
    assert status_full == "rejected"
    assert status_narrow == "rejected", (
        "on THIS study the control still catches it -- recorded so the limit of the "
        "falsification-scope safeguard is visible rather than assumed away"
    )


def test_scope_alone_can_decide_a_verdict():
    """Constructed, and labelled as such: the real study above is caught by
    the control regardless of scope, so it cannot by itself demonstrate
    that scope changes a final verdict.

    This is the case that does. Every window beats the control on deep
    samples; one middle out-of-sample fold breaches the pre-registered bar.
    Scored across all out-of-sample windows the hypothesis is REJECTED;
    scored on its final window alone it is CONFIRMED. Nothing differs but
    which data the mechanical rule was pointed at.
    """
    def w(idx, sharpe, oos=True):
        return WindowEvaluation(window_index=idx, is_out_of_sample=oos, metric_value=sharpe,
                                num_trades=250, p_value=0.001,
                                backtest_trace_id=idx * 2, significance_trace_id=idx * 2 + 1)

    windows = [w(0, 1.2, oos=False), w(1, 1.3), w(2, -0.8), w(3, 1.1)]

    status_full, _ = decide_status(windows, _REAL_CONDITION, 0.025)
    status_narrow, _ = decide_status([windows[-1]], _REAL_CONDITION, 0.025)

    assert status_full == "rejected"
    assert status_narrow == "confirmed"


def test_mean_aggregate_would_also_reject_but_hides_the_instability():
    """An aggregate reaches the right answer here for the wrong reason.

    The mean out-of-sample sharpe is about -0.008, which fails the bar --
    but it reports one mediocre number rather than "one fold was -1.51".
    Recorded so the choice of per-window over aggregate is visible as a
    deliberate one, not an accident of which happened to work.
    """
    oos = [e for e in _REAL_WALK_FORWARD if e.is_out_of_sample]
    mean = sum(e.metric_value for e in oos) / len(oos)
    assert mean < _REAL_CONDITION.threshold
    assert min(e.metric_value for e in oos) == -1.510
    assert mean > -0.5, "the mean masks how bad the worst fold actually was"


def test_in_sample_window_is_never_decisive():
    """Window 0 is the period the hypothesis was formed against -- the
    weakest evidence in the study. It must not drive the outcome in either
    direction.
    """
    # In-sample catastrophic, every out-of-sample window strong and deep.
    evals = [
        WindowEvaluation(window_index=0, is_out_of_sample=False, metric_value=-5.0,
                         num_trades=200, p_value=0.99, backtest_trace_id=1, significance_trace_id=2),
        WindowEvaluation(window_index=1, is_out_of_sample=True, metric_value=1.4,
                         num_trades=120, p_value=0.001, backtest_trace_id=3, significance_trace_id=4),
    ]
    status, _ = decide_status(evals, _REAL_CONDITION, 0.025)
    assert status == "confirmed"


# ---------------------------------------------------------------------------
# Gate ordering and the sample-adequacy asymmetry
# ---------------------------------------------------------------------------


def _strong(window_index, trades):
    return WindowEvaluation(window_index=window_index, is_out_of_sample=True, metric_value=1.4,
                            num_trades=trades, p_value=0.001,
                            backtest_trace_id=10 + window_index, significance_trace_id=20 + window_index)


def _in_sample():
    return WindowEvaluation(window_index=0, is_out_of_sample=False, metric_value=1.2,
                            num_trades=100, p_value=0.01, backtest_trace_id=1, significance_trace_id=2)


def test_thin_evidence_downgrades_a_confirmation_to_inconclusive():
    evals = [_in_sample(), _strong(1, MIN_TRADES_FOR_CONFIRMATION - 1)]
    status, _ = decide_status(evals, _REAL_CONDITION, 0.025)
    assert status == "inconclusive"


def test_thin_evidence_never_rescues_a_failure():
    """The asymmetry, stated as a test. A window that both fails the bar
    AND has too few trades must stay rejected -- 'not enough data to be
    sure' must never become an escape hatch from evidence of failure.
    """
    failing_and_thin = WindowEvaluation(
        window_index=1, is_out_of_sample=True, metric_value=-2.0, num_trades=3,
        p_value=0.99, backtest_trace_id=3, significance_trace_id=4,
    )
    status, _ = decide_status([_in_sample(), failing_and_thin], _REAL_CONDITION, 0.025)
    assert status == "rejected"


def test_deep_sample_confirms_when_both_gates_pass():
    status, _ = decide_status([_in_sample(), _strong(1, 200)], _REAL_CONDITION, 0.025)
    assert status == "confirmed"


def test_failing_the_control_alone_rejects():
    """Beating the pre-registered bar is not enough. A rule can post a fine
    Sharpe and still be indistinguishable from randomized entries at the
    same trade frequency -- which is the whole reason the control is
    mandatory.
    """
    passes_bar_fails_control = WindowEvaluation(
        window_index=1, is_out_of_sample=True, metric_value=1.4, num_trades=200,
        p_value=0.40, backtest_trace_id=3, significance_trace_id=4,
    )
    status, gates = decide_status([_in_sample(), passes_bar_fails_control], _REAL_CONDITION, 0.025)
    assert status == "rejected"
    assert {g.name: g for g in gates}["mandatory_control"].windows_failing == [1]


def test_missing_out_of_sample_evidence_is_inconclusive_not_confirmed():
    evals = [_in_sample(), WindowEvaluation(window_index=1, is_out_of_sample=True, metric_value=None,
                                            num_trades=0, p_value=None,
                                            backtest_trace_id=None, significance_trace_id=None)]
    status, _ = decide_status(evals, _REAL_CONDITION, 0.025)
    assert status == "inconclusive"


# ---------------------------------------------------------------------------
# FABRICATION -- five ways to try
# ---------------------------------------------------------------------------


def test_a_claim_matching_its_trace_validates():
    claims = [Claim(statement="in-sample sharpe was 0.771", tool_call_trace_id=101,
                    metric="sharpe_ratio", value=0.771)]
    assert validate_claims(claims, _real_traces()) == []


def test_a_claim_with_a_dangling_reference_is_rejected():
    claims = [Claim(statement="sharpe was 0.771", tool_call_trace_id=999,
                    metric="sharpe_ratio", value=0.771)]
    errors = validate_claims(claims, _real_traces())
    assert errors and "does not exist" in errors[0]


def test_a_claim_whose_value_does_not_match_its_trace_is_rejected():
    """The core fabrication case: a real reference, a real metric, an
    invented number.
    """
    claims = [Claim(statement="sharpe was 2.5", tool_call_trace_id=101,
                    metric="sharpe_ratio", value=2.5)]
    errors = validate_claims(claims, _real_traces())
    assert errors and "recorded" in errors[0]


def test_a_claim_citing_a_metric_absent_from_its_trace_is_rejected():
    """Why Claim carries `metric`: without it, checking `value` would mean
    matching against ANY numeric field in the trace, so a sharpe claim
    could validate against a p_value that happened to be close.
    """
    claims = [Claim(statement="p was 0.771", tool_call_trace_id=101,
                    metric="p_value", value=0.771)]
    errors = validate_claims(claims, _real_traces())
    assert errors and "absent from trace" in errors[0]


def test_a_claim_referencing_another_studys_trace_is_rejected():
    """by_id is built only from THIS run's traces, so a real trace id from
    a different study resolves to nothing. Without that scoping a model
    could support a claim with someone else's evidence.
    """
    other_study = [_FakeTrace(777, "run_backtest", {"sharpe_ratio": 3.0, "num_trades": 400})]
    claims = [Claim(statement="sharpe was 3.0", tool_call_trace_id=777,
                    metric="sharpe_ratio", value=3.0)]
    assert validate_claims(claims, other_study) == []          # valid in its own run
    errors = validate_claims(claims, _real_traces())           # not in this one
    assert errors and "does not exist" in errors[0]


def test_a_claim_referencing_an_errored_trace_is_rejected():
    traces = [_FakeTrace(200, "run_backtest", {"error": "boom"}, is_error=True)]
    claims = [Claim(statement="sharpe was 1.0", tool_call_trace_id=200,
                    metric="sharpe_ratio", value=1.0)]
    errors = validate_claims(claims, traces)
    assert errors and "recorded an error" in errors[0]


# ---------------------------------------------------------------------------
# The orphan-number scan -- the hole validate_claims alone leaves open
# ---------------------------------------------------------------------------


def test_a_fabricated_number_in_the_prose_is_caught_despite_clean_claims():
    """The subtle fabrication: keep the claims list scrupulously honest and
    slip an invented figure into the narrative, where nothing references it.
    """
    claims = [Claim(statement="sharpe was 0.771", tool_call_trace_id=101,
                    metric="sharpe_ratio", value=0.771)]
    narrative = "In-sample sharpe was 0.771, and the strategy returned 42.7% annually."
    orphans = scan_for_unreferenced_numbers(narrative, claims, allowed=set())
    assert any("42.7" in o for o in orphans)


def test_claimed_numbers_pass_the_scan():
    claims = [Claim(statement="sharpe was 0.771", tool_call_trace_id=101,
                    metric="sharpe_ratio", value=0.771)]
    assert scan_for_unreferenced_numbers("Sharpe was 0.771.", claims, allowed=set()) == []


def test_structural_numbers_pass_the_scan():
    assert scan_for_unreferenced_numbers(
        "This is hypothesis 1 of 4 windows.", [], allowed={1.0, 4.0}
    ) == []


def test_rounding_is_tolerated_but_a_different_number_is_not():
    claims = [Claim(statement="sharpe", tool_call_trace_id=101, metric="sharpe_ratio", value=0.6519)]
    assert scan_for_unreferenced_numbers("Sharpe was 0.65.", claims, allowed=set()) == []
    assert scan_for_unreferenced_numbers("Sharpe was 0.85.", claims, allowed=set()) != []


# ---------------------------------------------------------------------------
# The grounding prior
# ---------------------------------------------------------------------------


def test_ungrounded_faces_a_strictly_harder_bar_than_grounded():
    """docs/architecture.md: "an ungrounded hypothesis is closer to random
    search, so it faces a STRICTER bar". The ordering is the requirement;
    the exact multipliers are provisional.
    """
    local = corrected_threshold(1, "local_corpus")
    whitelist = corrected_threshold(1, "whitelist_search")
    none = corrected_threshold(1, "none")
    assert none < whitelist < local


def test_threshold_tightens_as_more_hypotheses_are_tested():
    assert corrected_threshold(10, "local_corpus") < corrected_threshold(1, "local_corpus")


def test_burden_values_are_the_documented_provisional_ones():
    """A canary, not a correctness claim. If these change, the reasoning in
    verdict.py's comment and the disclosure in mandatory_caveats must change
    with them -- this test exists so that cannot happen silently.
    """
    assert TIER_SEARCH_BURDEN == {"local_corpus": 1.0, "whitelist_search": 2.0, "none": 10.0}


# ---------------------------------------------------------------------------
# Mandatory caveats
# ---------------------------------------------------------------------------


def test_mandatory_caveats_disclose_count_threshold_and_provisional_assumption(monkeypatch):
    caveats = mandatory_caveats(3, "whitelist_search", 0.0083, _REAL_WALK_FORWARD, charter=None)
    joined = " ".join(caveats)
    assert "hypothesis 3" in joined
    assert "0.0083" in joined
    assert "provisional" in joined.lower()
    assert "point-in-time" in joined


def test_small_sample_caveat_appears_only_when_it_applies():
    thin = mandatory_caveats(1, "local_corpus", 0.05, _REAL_WALK_FORWARD, charter=None)
    assert any(str(MIN_TRADES_FOR_CONFIRMATION) in c and "trade" in c for c in thin)

    deep = mandatory_caveats(1, "local_corpus", 0.05, [_in_sample(), _strong(1, 500)], charter=None)
    assert not any("floor" in c for c in deep)
