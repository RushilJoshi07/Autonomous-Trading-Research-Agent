"""Regression coverage for eval/harness.py -- Stage 6, Component 2.

Tests _score ONLY -- the pure half. run_case's own control flow (does it
correctly convert "loop didn't complete" or a VerdictValidationError into
verdict=None before scoring) is deliberately NOT unit-tested here with a
faked MCP session and a faked database. render_verdict itself has never
been unit-tested that way anywhere in this project -- not even in Stage
5's own tests/agentic_core/test_verdict.py, which tests decide_status and
validate_claims (the pure pieces render_verdict calls) but never
render_verdict itself, because of the real DB I/O involved. Both
control-flow scenarios the harness needs to handle reduce to exactly one
thing from _score's point of view -- verdict is None -- so exercising that
directly here covers the same scoring consequence without building DB
fixture machinery this project has consistently chosen not to build for
this function.
"""

from datetime import date, datetime, timezone

import pytest

from agentic_core.schemas import (
    Charter,
    DateRange,
    EffectFamily,
    FalsificationCondition,
    Hypothesis,
    ParsedCharter,
    ParsedHypothesis,
    ParsedStudyDesign,
    ParsedVerdict,
    StudyDesign,
    UniverseFilter,
    Verdict,
)
from agentic_core.study_design import NULL_HYPOTHESIS
from backtester.schema import Comparison, Condition, PriceTerm, ScaledTerm, StrategyRule
from eval.golden_cases import GoldenCase
from eval.harness import GoldenSetReport, _score, _write_report

_RULE = StrategyRule(
    name="fake_rule",
    description="minimal rule for scoring-logic tests only -- never run through a backtest",
    entry=Condition(kind="leaf", comparison=Comparison(
        left=PriceTerm(field="close"), op="lt",
        right=ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=0.9),
    )),
    exit=Condition(kind="leaf", comparison=Comparison(
        left=PriceTerm(field="close"), op="gt",
        right=ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1.1),
    )),
)


def _fake_case(expected_status, expected_caveat_substring=None, name="fake_case", category="planted_true"):
    """A GoldenCase with schema-valid but otherwise arbitrary content.

    _score never reads case.charter/case.hypothesis/case.design/case.ticker
    -- only case.name, case.category, case.expected_status, and
    case.expected_caveat_substring -- so these three objects only need to
    satisfy Pydantic, not resemble a real fixture.
    """
    charter = Charter(
        parsed=ParsedCharter(universe=UniverseFilter(sector=None), hypothesis_families=[EffectFamily.MEAN_REVERSION]),
        resolved_universe=["FAKE"], screening_as_of=date(2020, 1, 1), screening_group_size=1,
    )
    hypothesis = Hypothesis(
        parsed=ParsedHypothesis(
            rule=_RULE,
            prediction="fake prediction for a scoring-logic test",
            falsification_condition=FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.5),
            rationale="fake rationale for a scoring-logic test",
        ),
        grounding_tier="none",
        citations=[],
    )
    design = StudyDesign(
        parsed=ParsedStudyDesign(design_type="simple_holdout", split="70/30", rationale="fake"),
        in_sample=DateRange(start=date(2020, 1, 1), end=date(2020, 6, 1)),
        out_of_sample=DateRange(start=date(2020, 7, 1), end=date(2020, 12, 1)),
        null_hypothesis=NULL_HYPOTHESIS,
    )
    return GoldenCase(
        name=name, category=category, ticker="FAKE",
        charter_id="charter-1", hypothesis_id="hyp-1", design_id="design-1",
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status=expected_status, expected_caveat_substring=expected_caveat_substring,
    )


def _fake_verdict(status, caveats=None):
    return Verdict(
        parsed=ParsedVerdict(narrative="fake narrative", claims=[]),
        status=status,
        hypothesis_count_under_charter=1,
        corrected_significance_threshold=0.05,
        caveats=caveats or [],
    )


# ---------------------------------------------------------------------------
# The happy path and the independence of the three dimensions
# ---------------------------------------------------------------------------


def test_all_three_pass_when_verdict_matches_expected_status():
    case = _fake_case(expected_status="confirmed")
    verdict = _fake_verdict(status="confirmed")
    result = _score(case, "run-1", verdict, "scored normally")
    assert result.status_correct
    assert result.fabrication_clean
    assert result.caveats_ok
    assert result.passed


def test_status_correct_is_independent_of_fabrication_clean():
    """A real verdict with the WRONG status: fabrication_clean stays True
    (a verdict genuinely was produced) while status_correct goes False --
    these are two different questions, not one combined check.
    """
    case = _fake_case(expected_status="confirmed")
    verdict = _fake_verdict(status="rejected")
    result = _score(case, "run-1", verdict, "scored normally")
    assert not result.status_correct
    assert result.fabrication_clean
    assert not result.passed


# ---------------------------------------------------------------------------
# verdict=None -- the shape BOTH "loop didn't complete" and "validation
# failed after retries" collapse into
# ---------------------------------------------------------------------------


def test_no_verdict_fails_status_correct_and_fabrication_clean():
    case = _fake_case(expected_status="confirmed")
    result = _score(case, None, None, "execution loop ended with status='failed'")
    assert result.actual_status is None
    assert not result.status_correct
    assert not result.fabrication_clean
    assert not result.passed


def test_no_verdict_from_exhausted_retries_scores_identically_to_a_loop_failure():
    """The exact scenario render_verdict's own VerdictValidationError
    produces -- run_case catches it and passes verdict=None here. Scored
    no differently from a loop that never completed: both mean "no
    verdict exists to trust", and there is no reason for _score to
    distinguish them.
    """
    case = _fake_case(expected_status="confirmed")
    result = _score(case, "run-2", None, "verdict validation failed after retries: [...]")
    assert not result.fabrication_clean
    assert not result.passed


def test_no_verdict_with_no_caveat_requirement_is_still_an_overall_failure():
    """caveats_ok is trivially True here (nothing was required) -- proving
    that a trivial pass on one dimension cannot rescue a case that fails
    the other two.
    """
    case = _fake_case(expected_status="rejected", expected_caveat_substring=None)
    result = _score(case, None, None, "execution loop raised: RuntimeError(...)")
    assert result.caveats_ok
    assert not result.passed


# ---------------------------------------------------------------------------
# caveats_ok -- exercised specifically for golden_caveat_thin_sample's shape
# ---------------------------------------------------------------------------


def test_caveat_passes_when_the_required_substring_is_present():
    case = _fake_case(expected_status="inconclusive", expected_caveat_substring="30-trade floor")
    verdict = _fake_verdict(status="inconclusive", caveats=["some other caveat", "below the 30-trade floor (11 trades)"])
    result = _score(case, "run-3", verdict, "scored normally")
    assert result.caveats_ok
    assert result.passed


def test_caveat_fails_when_the_verdict_has_caveats_but_not_the_required_one():
    """Status can be exactly right and the verdict can be perfectly
    fabrication-clean, and the case must still fail if the ONE required
    disclosure never actually appears.
    """
    case = _fake_case(expected_status="inconclusive", expected_caveat_substring="30-trade floor")
    verdict = _fake_verdict(status="inconclusive", caveats=["an unrelated caveat about universe bias"])
    result = _score(case, "run-4", verdict, "scored normally")
    assert result.status_correct
    assert result.fabrication_clean
    assert not result.caveats_ok
    assert not result.passed


def test_caveat_fails_when_there_is_no_verdict_to_check_it_against():
    case = _fake_case(expected_status="inconclusive", expected_caveat_substring="30-trade floor")
    result = _score(case, None, None, "execution loop ended with status='failed'")
    assert not result.caveats_ok
    assert not result.passed


# ---------------------------------------------------------------------------
# CaseResult denormalization and passthrough
# ---------------------------------------------------------------------------


def test_case_result_denormalizes_name_and_category():
    case = _fake_case(expected_status="rejected", name="my_case", category="planted_false")
    result = _score(case, None, None, "x")
    assert result.name == "my_case"
    assert result.category == "planted_false"
    assert result.expected_status == "rejected"


def test_detail_passes_through_unchanged():
    case = _fake_case(expected_status="confirmed")
    verdict = _fake_verdict(status="confirmed")
    result = _score(case, "run-5", verdict, "scored normally")
    assert result.detail == "scored normally"


# ---------------------------------------------------------------------------
# Report serialization -- light, not central
# ---------------------------------------------------------------------------


def test_write_report_round_trips_through_real_json(tmp_path):
    case = _fake_case(expected_status="confirmed")
    verdict = _fake_verdict(status="confirmed")
    result = _score(case, "run-6", verdict, "scored normally")
    report = GoldenSetReport(
        run_at=datetime.now(timezone.utc),
        results=[result], total=1, passed=1, construction_errors=[],
    )
    path = _write_report(report, out_dir=tmp_path)
    reloaded = GoldenSetReport.model_validate_json(path.read_text())
    assert reloaded.total == 1
    assert reloaded.passed == 1
    assert reloaded.results[0].name == "fake_case"
