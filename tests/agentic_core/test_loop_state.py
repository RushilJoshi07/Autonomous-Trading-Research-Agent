"""Regression coverage for agentic_core/loop_state.py -- Stage 5,
Component 6a.

The tests here are deliberately ADVERSARIAL, not confirming. Stage 2's
sacred gate was satisfied by attempting lookahead and proving the engine
refused it; this is that same discipline applied to the reasoning loop for
the first time. Every guarantee is tested by trying to violate it.

Three properties are proven structurally -- against the JSON schema the
model actually receives, not against observed behavior:
  1. no date or rule field exists for the LLM to fill in (lookahead),
  2. diagnostic tools are absent from the enum until evidence exists,
  3. advance/conclude are absent from the union until a window's evidence
     is complete.

A schema-level assertion is stronger than a behavioral one here: behavior
proves the model did not do X on this run, while the schema proves X was
not expressible.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from agentic_core.loop_state import (
    DIAGNOSTIC_TOOLS,
    EVIDENCE_TOOLS,
    LoopState,
    ToolResult,
    available_tools,
    build_decision_model,
    can_advance,
    can_conclude,
    flatten_windows,
    window_evidence_complete,
)
from agentic_core.schemas import (
    Charter,
    DateRange,
    FalsificationCondition,
    Hypothesis,
    ParsedCharter,
    ParsedHypothesis,
    ParsedStudyDesign,
    StudyDesign,
    UniverseFilter,
)
from backtester.schema import SMA_CROSSOVER


def _charter(tickers=("AAPL", "MSFT")) -> Charter:
    return Charter(
        parsed=ParsedCharter(
            universe=UniverseFilter(sector="Technology"),
            hypothesis_families=["momentum"],
        ),
        resolved_universe=list(tickers),
        screening_as_of=date(2026, 1, 1),
        screening_group_size=50,
    )


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        parsed=ParsedHypothesis(
            rule=SMA_CROSSOVER,
            prediction="SMA crossover produces positive risk-adjusted returns.",
            falsification_condition=FalsificationCondition(
                metric="sharpe_ratio", comparison="less_than", threshold=0.5
            ),
            rationale="Grounded in Brock, Lakonishok & LeBaron (1992).",
        ),
        grounding_tier="local_corpus",
        citations=[],
    )


def _design(n_windows: int = 2) -> StudyDesign:
    """n_windows == 2 gives a simple_holdout; more gives a walk_forward with
    (n_windows - 1) out-of-sample folds.
    """
    windows = [
        DateRange(start=date(2010 + i, 1, 1), end=date(2010 + i, 12, 31))
        for i in range(n_windows)
    ]
    if n_windows == 2:
        return StudyDesign(
            parsed=ParsedStudyDesign(design_type="simple_holdout", split="70/30", rationale="x"),
            in_sample=windows[0],
            out_of_sample=windows[1],
            null_hypothesis="null",
        )
    return StudyDesign(
        parsed=ParsedStudyDesign(
            design_type="walk_forward", split="70/30", walk_forward_folds=n_windows - 1, rationale="x"
        ),
        in_sample=windows[0],
        out_of_sample=windows[1],
        walk_forward_windows=windows,
        null_hypothesis="null",
    )


def _state(n_windows=2, window_index=0, results=None) -> LoopState:
    design = _design(n_windows)
    return LoopState(
        study_run_id="run-1",
        charter=_charter(),
        hypothesis=_hypothesis(),
        design=design,
        windows=flatten_windows(design),
        window_index=window_index,
        step_count=0,
        pending_action=None,
        results=results or [],
        status="running",
        failure_reason=None,
    )


def _result(tool: str, window_index: int = 0, is_error: bool = False) -> ToolResult:
    return ToolResult(
        step_index=0,
        window_index=window_index,
        tool_name=tool,
        arguments={},
        result={},
        is_error=is_error,
    )


def _evidence_complete(window_index: int = 0) -> list[ToolResult]:
    return [
        _result("run_backtest", window_index),
        _result("test_significance", window_index),
    ]


def _call_tool_schema(state: LoopState) -> dict:
    """The CallTool variant's own schema, dug out of AgentDecision's $defs."""
    schema = build_decision_model(state).model_json_schema()
    return schema["$defs"]["CallTool"]


# ---------------------------------------------------------------------------
# Guarantee 1 -- lookahead is not expressible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", ["start", "end", "rule", "commission", "cash", "seed", "n_resamples"])
def test_action_schema_has_no_field_for_dates_or_rule(forbidden):
    """The lookahead guarantee, proven against the schema itself.

    If this ever fails, someone has added a field the LLM can use to name
    its own date range or substitute its own rule -- which would make
    lookahead expressible again no matter what execute_tool does with the
    value. Do not "fix" this by filtering the field out downstream; the
    guarantee is that it does not exist.
    """
    props = _call_tool_schema(_state())["properties"]
    assert forbidden not in props


def test_action_model_rejects_an_invented_date_field():
    """extra='forbid' means a model that tries to smuggle in a date fails
    loudly rather than having it silently dropped -- a dropped field would
    leave the action looking clean while the intent went unrecorded.
    """
    model = build_decision_model(_state())
    with pytest.raises(ValidationError):
        model.model_validate(
            {"decision": {
                "action": "call_tool", "tool": "run_backtest", "ticker": "AAPL",
                "reasoning": "peek", "start": "2099-01-01",
            }}
        )


def test_ticker_is_constrained_to_the_charter_universe():
    """A ticker outside resolved_universe would be testing outside the
    pre-registered population.
    """
    model = build_decision_model(_state())
    with pytest.raises(ValidationError):
        model.model_validate(
            {"decision": {"action": "call_tool", "tool": "run_backtest", "ticker": "TSLA", "reasoning": "x"}}
        )


def test_indicator_is_constrained_to_the_rules_own_indicators():
    state = _state(results=_evidence_complete())
    model = build_decision_model(state)
    with pytest.raises(ValidationError):
        model.model_validate(
            {"decision": {
                "action": "call_tool", "tool": "compute_indicator", "ticker": "AAPL",
                "indicator": "ADX", "reasoning": "x",
            }}
        )


# ---------------------------------------------------------------------------
# Guarantee 2 -- evidence before diagnosis
# ---------------------------------------------------------------------------


def test_diagnostic_tools_absent_from_enum_before_any_evidence():
    enum = _call_tool_schema(_state())["properties"]["tool"]["enum"]
    assert set(enum) == set(EVIDENCE_TOOLS)
    for tool in DIAGNOSTIC_TOOLS:
        assert tool not in enum


def test_diagnostic_tools_present_after_successful_evidence():
    state = _state(results=[_result("run_backtest")])
    enum = _call_tool_schema(state)["properties"]["tool"]["enum"]
    for tool in DIAGNOSTIC_TOOLS:
        assert tool in enum


def test_errored_evidence_does_not_unlock_diagnostics():
    """Otherwise "make the backtest fail" becomes a way through the gate."""
    state = _state(results=[_result("run_backtest", is_error=True)])
    assert available_tools(state) == EVIDENCE_TOOLS


def test_evidence_in_a_previous_window_does_not_unlock_the_next_one():
    """Each window earns its own diagnostics. Carrying the unlock forward
    would let fold 3 be diagnosed on the strength of fold 1's backtest.
    """
    state = _state(n_windows=4, window_index=1, results=_evidence_complete(window_index=0))
    assert available_tools(state) == EVIDENCE_TOOLS


def test_hostile_llm_cannot_call_a_diagnostic_tool_first():
    """The adversarial case: a model that always reaches for the regime
    classifier before running anything. The call is not rejected by a
    guard -- the tool has no name in the schema to emit.
    """
    model = build_decision_model(_state())
    with pytest.raises(ValidationError):
        model.model_validate(
            {"decision": {
                "action": "call_tool", "tool": "classify_regime", "ticker": "AAPL", "reasoning": "investigate",
            }}
        )


# ---------------------------------------------------------------------------
# Guarantee 3 -- no window left untested
# ---------------------------------------------------------------------------


def test_cannot_advance_without_evidence():
    assert can_advance(_state(n_windows=3)) is False


def test_cannot_advance_with_backtest_but_no_control():
    """The mandatory control, enforced as an exit condition. A window where
    run_backtest succeeded but test_significance never ran must not be
    leavable.
    """
    state = _state(n_windows=3, results=[_result("run_backtest")])
    assert can_advance(state) is False


def test_can_advance_once_both_evidence_tools_succeeded():
    assert can_advance(_state(n_windows=3, results=_evidence_complete())) is True


def test_cannot_advance_from_the_last_window():
    state = _state(n_windows=3, window_index=2, results=_evidence_complete(window_index=2))
    assert can_advance(state) is False


def test_cannot_conclude_while_unvisited_windows_remain():
    """The truncation attack: satisfy window 0, then conclude, skipping
    every remaining fold and gutting the decay check the walk_forward
    design existed for.
    """
    state = _state(n_windows=5, window_index=0, results=_evidence_complete())
    assert can_conclude(state) is False


def test_cannot_conclude_on_the_last_window_without_its_own_evidence():
    """The subtler truncation: advance into the final fold and conclude
    immediately, leaving it reachable but never tested.
    """
    state = _state(n_windows=3, window_index=2, results=_evidence_complete(0) + _evidence_complete(1))
    assert can_conclude(state) is False


def test_can_conclude_on_last_window_with_evidence():
    state = _state(n_windows=2, window_index=1, results=_evidence_complete(window_index=1))
    assert can_conclude(state) is True


def test_hostile_llm_cannot_conclude_early():
    """A model that wants to stop after the in-sample window has no
    'conclude' variant in its union at all.
    """
    model = build_decision_model(_state(n_windows=5, results=_evidence_complete()))
    with pytest.raises(ValidationError):
        model.model_validate({"decision": {"action": "conclude", "reasoning": "looks good enough"}})


def test_hostile_llm_cannot_advance_without_running_the_control():
    model = build_decision_model(_state(n_windows=3, results=[_result("run_backtest")]))
    with pytest.raises(ValidationError):
        model.model_validate({"decision": {"action": "advance_phase", "reasoning": "move on"}})


# ---------------------------------------------------------------------------
# The union always offers a legal move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_windows,window_index,results",
    [
        (2, 0, []),
        (2, 0, _evidence_complete()),
        (2, 1, _evidence_complete(1)),
        (5, 3, []),
    ],
)
def test_a_legal_action_always_exists(n_windows, window_index, results):
    """No state may leave the model with nothing it can legally emit --
    that would be a deadlock rather than a guardrail. Evidence tools are
    unconditional precisely to guarantee this.
    """
    state = _state(n_windows=n_windows, window_index=window_index, results=results)
    assert len(available_tools(state)) > 0
    model = build_decision_model(state)
    ok = model.model_validate(
        {"decision": {"action": "call_tool", "tool": "run_backtest", "ticker": "AAPL", "reasoning": "start"}}
    )
    assert ok.decision.tool == "run_backtest"


# ---------------------------------------------------------------------------
# flatten_windows
# ---------------------------------------------------------------------------


def test_flatten_windows_simple_holdout_is_two_windows():
    assert len(flatten_windows(_design(2))) == 2


def test_flatten_windows_walk_forward_does_not_duplicate_in_sample():
    """walk_forward_windows already contains in_sample as element 0;
    concatenating it again would test the in-sample period twice and shift
    every window index by one.
    """
    design = _design(5)
    windows = flatten_windows(design)
    assert len(windows) == 5
    assert windows[0] == design.in_sample
    assert windows[1] == design.out_of_sample


def test_window_evidence_complete_is_per_window():
    state = _state(n_windows=3, results=_evidence_complete(window_index=1))
    assert window_evidence_complete(state, 1) is True
    assert window_evidence_complete(state, 0) is False
