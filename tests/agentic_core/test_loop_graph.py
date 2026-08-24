"""Regression coverage for agentic_core/loop_graph.py -- Stage 5,
Component 6a.

test_loop_state.py proves the guarantees hold in the SCHEMA. This file
proves they hold in the RUNNING GRAPH, driven by a deliberately lazy agent
that always takes the earliest exit available to it. That agent is the
adversary: an agreeable model looking for the shortest path to "done" is
exactly the failure mode .claude/rules/agent-honesty.md names, and the
useful question is not whether it behaves, but whether the graph lets it
misbehave.

Everything here runs with no network, no MCP subprocess, and no Bedrock
spend -- a fake session returns canned tool results and a scripted callable
stands in for the LLM. That is the whole reason build_graph takes both as
arguments instead of importing them.
"""

import asyncio
import uuid
from datetime import date, datetime

import pytest

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.loop_graph import MAX_DECISION_ATTEMPTS, MAX_STEPS, build_graph, initial_state
from agentic_core.loop_state import build_decision_model, can_advance, can_conclude
from tests.agentic_core.test_loop_state import _charter, _design, _hypothesis


class FakeToolResponse:
    """Mirrors the two attributes loop_graph reads off a real MCP
    CallToolResult (verified against Stage 4's own client usage in
    scripts/verify_stage4_gate.py).
    """

    def __init__(self, payload: dict, is_error: bool = False):
        self.structured_content = payload
        self.is_error = is_error


class FakeSession:
    """Records every (name, arguments) pair so tests can assert on what the
    loop ACTUALLY sent to the tool layer -- which is where window injection
    either happened or did not.
    """

    def __init__(self, *, fail_tools: set[str] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_tools = fail_tools or set()

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name in self.fail_tools:
            return FakeToolResponse({"error": "simulated failure"}, is_error=True)
        return FakeToolResponse({"sharpe_ratio": 1.1, "p_value": 0.03, "num_trades": 42})


class LazyAgent:
    """The adversary: always takes the earliest exit available.

    Conclude if it is allowed to, else advance if it is allowed to, else do
    the minimum evidence work that unlocks an exit. A real agreeable model
    would behave exactly this way when it wants to be done. If the graph is
    correct, this agent still cannot skip a window or skip the control --
    not because it chose well, but because the alternatives were never in
    its schema.
    """

    def __init__(self):
        self.decisions: list[str] = []

    def __call__(self, prompt: str, response_model):
        # The agent inspects only what the schema offers -- exactly the
        # information a real model gets. It never sees loop internals.
        variants = _offered_actions(response_model)
        if "conclude" in variants:
            payload = {"action": "conclude", "reasoning": "done"}
        elif "advance_phase" in variants:
            payload = {"action": "advance_phase", "reasoning": "next window"}
        else:
            tool = _first_missing_evidence(response_model, prompt)
            payload = {"action": "call_tool", "tool": tool, "ticker": "AAPL", "reasoning": "minimum work"}
        self.decisions.append(payload["action"])
        return response_model.model_validate({"decision": payload})


def _offered_actions(response_model) -> set[str]:
    schema = response_model.model_json_schema()
    return {
        const
        for name, defn in schema.get("$defs", {}).items()
        for const in [defn.get("properties", {}).get("action", {}).get("const")]
        if const
    }


def _first_missing_evidence(response_model, prompt: str) -> str:
    """run_backtest first, then test_significance -- the minimum sequence
    that unlocks an exit. Reads the prompt to know what already ran, the
    same way a real model would.
    """
    if "run_backtest" not in prompt.split("Results in the current window:")[-1]:
        return "run_backtest"
    return "test_significance"


def _seed(session, n_windows: int) -> tuple[str, str, object, object, object]:
    charter, hypothesis, design = _charter(), _hypothesis(), _design(n_windows)
    charter_id, hyp_id, design_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    session.add(CharterRow(
        id=charter_id, mandate_text="m", charter=charter.model_dump(mode="json"),
        confirmed=True, created_at=datetime.now(),
    ))
    session.flush()
    session.add(HypothesisRow(
        id=hyp_id, charter_id=charter_id,
        rule=hypothesis.parsed.rule.model_dump(mode="json"),
        prediction=hypothesis.parsed.prediction,
        falsification_condition=hypothesis.parsed.falsification_condition.model_dump(mode="json"),
        rationale=hypothesis.parsed.rationale, citations=[],
        grounding_tier="local_corpus", status="proposed", created_at=datetime.now(),
    ))
    session.flush()
    session.add(StudyDesignRow(
        id=design_id, hypothesis_id=hyp_id,
        design=design.model_dump(mode="json"), created_at=datetime.now(),
    ))
    session.commit()
    return hyp_id, design_id, charter, hypothesis, design


def _run(session, n_windows=2, agent=None, fail_tools=None):
    hyp_id, design_id, charter, hypothesis, design = _seed(session, n_windows)
    fake = FakeSession(fail_tools=fail_tools)
    agent = agent or LazyAgent()
    graph = build_graph(lambda: fake, agent, design_id=design_id, hypothesis_id=hyp_id)
    state = initial_state(charter, hypothesis, design)
    # recursion_limit is a TEST backstop, not a production requirement --
    # LangGraph 1.2.11's default is 10007 (verified in
    # langgraph/_internal/_config.py), which a MAX_STEPS-bounded run never
    # approaches. It is lowered here so that a regression which breaks the
    # budget check fails this suite in seconds instead of grinding through
    # ten thousand supersteps.
    final = asyncio.run(graph.ainvoke(state, {"recursion_limit": 200}))
    return final, fake, agent, hyp_id


# ---------------------------------------------------------------------------
# Window injection -- the lookahead boundary, in the running graph
# ---------------------------------------------------------------------------


def test_every_tool_call_carries_the_current_windows_dates(loop_db_session):
    """The action object has no date field, so these dates can only have
    come from state. Asserting on what the fake session RECEIVED is the
    strongest available form of this check: it is the exact payload a real
    MCP tool would have been handed.
    """
    final, fake, _, _ = _run(loop_db_session, n_windows=3)
    windows = final["windows"]
    seen_per_window: dict[tuple[str, str], int] = {}
    for _, args in fake.calls:
        seen_per_window[(args["start"], args["end"])] = seen_per_window.get((args["start"], args["end"]), 0) + 1

    expected = {(w.start.isoformat(), w.end.isoformat()) for w in windows}
    assert set(seen_per_window) == expected, "a call used dates belonging to no window"


def test_no_call_ever_uses_a_later_windows_dates_while_in_an_earlier_one(loop_db_session):
    """The lookahead attempt, stated as the property that actually matters:
    calls must appear in window order, never reaching forward.
    """
    final, fake, _, _ = _run(loop_db_session, n_windows=4)
    windows = final["windows"]
    order = {w.start.isoformat(): i for i, w in enumerate(windows)}
    indices = [order[args["start"]] for _, args in fake.calls]
    assert indices == sorted(indices), f"window order violated: {indices}"


def test_the_frozen_rule_is_injected_not_chosen(loop_db_session):
    _, fake, _, _ = _run(loop_db_session)
    rule_calls = [args for name, args in fake.calls if name in {"run_backtest", "test_significance"}]
    assert rule_calls
    for args in rule_calls:
        assert args["rule"]["name"] == "sma_10_30_crossover"


# ---------------------------------------------------------------------------
# The lazy agent cannot skip anything
# ---------------------------------------------------------------------------


def test_lazy_agent_still_runs_the_control_in_every_window(loop_db_session):
    """The headline test. An agent that wants to stop as early as possible
    is still forced through run_backtest AND test_significance in every
    single window -- including the last one.
    """
    final, fake, _, _ = _run(loop_db_session, n_windows=4)
    windows = final["windows"]
    by_window = {w.start.isoformat(): set() for w in windows}
    for name, args in fake.calls:
        by_window[args["start"]].add(name)
    for start, tools in by_window.items():
        assert "run_backtest" in tools, f"window starting {start} never ran a backtest"
        assert "test_significance" in tools, f"window starting {start} never ran the control"


def test_lazy_agent_visits_every_window(loop_db_session):
    final, _, agent, _ = _run(loop_db_session, n_windows=5)
    assert final["window_index"] == 4
    assert agent.decisions.count("advance_phase") == 4


def test_completed_run_is_recorded(loop_db_session):
    final, _, _, hyp_id = _run(loop_db_session, n_windows=2)
    assert final["status"] == "completed"
    row = loop_db_session.get(StudyRunRow, final["study_run_id"])
    loop_db_session.refresh(row)
    assert row.status == "completed"
    assert row.finished_at is not None


def test_hypothesis_is_left_testing_for_component_7(loop_db_session):
    """Component 6 never decides an outcome. Setting confirmed/rejected is
    Component 7's job, applying the pre-registered falsification condition
    mechanically -- if this loop set a verdict, that separation would be
    gone.
    """
    _, _, _, hyp_id = _run(loop_db_session)
    row = loop_db_session.get(HypothesisRow, hyp_id)
    loop_db_session.refresh(row)
    assert row.status == "testing"


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


def test_every_call_is_traced_with_its_window_index(loop_db_session):
    final, fake, _, _ = _run(loop_db_session, n_windows=3)
    traces = loop_db_session.query(ToolCallTraceRow).filter_by(
        study_run_id=final["study_run_id"]
    ).order_by(ToolCallTraceRow.step_index).all()
    assert len(traces) == len(fake.calls)
    assert [t.window_index for t in traces] == sorted(t.window_index for t in traces)
    windows = final["windows"]
    for t in traces:
        assert t.arguments["start"] == windows[t.window_index].start.isoformat()


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_exhaustion_fails_the_run_rather_than_concluding(loop_db_session):
    """A study that ran out of budget has untested windows. Concluding
    anyway would be a confident verdict drawn from partial folds -- exactly
    the dishonest output this project exists to prevent. status='failed' is
    the honest outcome.
    """
    final, _, _, _ = _run(loop_db_session, n_windows=3, fail_tools={"test_significance"})
    assert final["status"] == "failed"
    assert final["step_count"] > MAX_STEPS
    row = loop_db_session.get(StudyRunRow, final["study_run_id"])
    loop_db_session.refresh(row)
    assert row.status == "failed"


# ---------------------------------------------------------------------------
# Bounded retry-with-feedback
# ---------------------------------------------------------------------------


class _FakeBlock:
    type = "tool_use"

    def __init__(self, payload):
        self.input = payload


class _FakeResponse:
    def __init__(self, payload):
        self.content = [_FakeBlock(payload)]


def _structured_error(payload, message="rejected"):
    from llm_client import StructuredOutputError

    return StructuredOutputError(message, validation_error=None, raw_response=_FakeResponse(payload))


class FlakyAgent(LazyAgent):
    """Fails the first `n_failures` calls with the EXACT malformed shape
    Claude produced live -- the nested decision object serialized as a
    JSON string, with a trailing brace making it invalid JSON.
    """

    def __init__(self, n_failures: int, payload: str | dict | None = None):
        super().__init__()
        self.remaining = n_failures
        self.payload = payload if payload is not None else _REAL_MALFORMED
        self.attempts = 0
        self.prompts: list[str] = []

    def __call__(self, prompt, response_model):
        self.attempts += 1
        self.prompts.append(prompt)
        if self.remaining > 0:
            self.remaining -= 1
            raise _structured_error({"decision": self.payload})
        return super().__call__(prompt, response_model)


# Reproduced from the real Bedrock failure in Component 6b's first two live
# runs: the extra trailing '}' is what made json.loads fail, which is why
# the stringified-decision validator alone did not save the run.
_REAL_MALFORMED = (
    '{"action": "advance_phase", "reasoning": "In-sample testing is complete. '
    'Proceeding to the out-of-sample window (window 1 of 2)."}}'
)


def test_retry_recovers_from_the_real_malformed_output(loop_db_session):
    """The live failure, reproduced exactly and then survived."""
    hyp_id, design_id, charter, hypothesis, design = _seed(loop_db_session, 2)
    fake = FakeSession()
    agent = FlakyAgent(n_failures=1)
    graph = build_graph(lambda: fake, agent, design_id=design_id, hypothesis_id=hyp_id)
    final = asyncio.run(graph.ainvoke(initial_state(charter, hypothesis, design), {"recursion_limit": 200}))

    assert final["status"] == "completed"
    assert len(final["rejections"]) == 1
    assert final["rejections"][0].kind == "encoding"


def test_every_retry_attempt_costs_a_step(loop_db_session):
    """The budget is a COST control: its unit is LLM calls made. If retries
    were free, a persistently malformed model would generate unbounded
    billable calls while step_count stayed frozen.
    """
    hyp_id, design_id, charter, hypothesis, design = _seed(loop_db_session, 2)
    clean = FakeSession()
    baseline_agent = LazyAgent()
    graph = build_graph(lambda: clean, baseline_agent, design_id=design_id, hypothesis_id=hyp_id)
    baseline = asyncio.run(graph.ainvoke(initial_state(charter, hypothesis, design), {"recursion_limit": 200}))

    hyp2, design2, charter2, hypothesis2, design_obj2 = _seed(loop_db_session, 2)
    flaky = FlakyAgent(n_failures=2)
    graph2 = build_graph(lambda: FakeSession(), flaky, design_id=design2, hypothesis_id=hyp2)
    retried = asyncio.run(graph2.ainvoke(initial_state(charter2, hypothesis2, design_obj2), {"recursion_limit": 200}))

    assert retried["step_count"] == baseline["step_count"] + 2


def test_exhausted_retries_fail_cleanly_instead_of_crashing(loop_db_session):
    """Before retry existed, a malformed output propagated and crashed the
    graph, leaving StudyRun.status='running' with orphaned traces and no
    recorded reason -- observed twice on real runs. It must now be a
    recorded failure.
    """
    hyp_id, design_id, charter, hypothesis, design = _seed(loop_db_session, 2)
    agent = FlakyAgent(n_failures=99)
    graph = build_graph(lambda: FakeSession(), agent, design_id=design_id, hypothesis_id=hyp_id)
    final = asyncio.run(graph.ainvoke(initial_state(charter, hypothesis, design), {"recursion_limit": 200}))

    assert final["status"] == "failed"
    assert final["failure_reason"] is not None
    assert len(final["rejections"]) == MAX_DECISION_ATTEMPTS
    row = loop_db_session.get(StudyRunRow, final["study_run_id"])
    loop_db_session.refresh(row)
    assert row.status == "failed"
    assert row.finished_at is not None


def test_retry_feedback_names_what_was_wrong_and_what_is_available(loop_db_session):
    hyp_id, design_id, charter, hypothesis, design = _seed(loop_db_session, 2)
    agent = FlakyAgent(n_failures=1)
    graph = build_graph(lambda: FakeSession(), agent, design_id=design_id, hypothesis_id=hyp_id)
    asyncio.run(graph.ainvoke(initial_state(charter, hypothesis, design), {"recursion_limit": 200}))

    retry_prompt = agent.prompts[1]
    assert "REJECTED" in retry_prompt
    assert "nested JSON OBJECT" in retry_prompt
    assert "Actions available to you right now" in retry_prompt


def test_a_guarantee_violation_is_recorded_as_such_not_as_encoding(loop_db_session):
    """A model genuinely attempting a forbidden action is evidence for
    Sacred Gate 2 -- the guard firing against a real decision, not a fake
    one. It must not be filed as serialization noise.
    """
    hyp_id, design_id, charter, hypothesis, design = _seed(loop_db_session, 3)
    agent = FlakyAgent(n_failures=1, payload={"action": "conclude", "reasoning": "done early"})
    graph = build_graph(lambda: FakeSession(), agent, design_id=design_id, hypothesis_id=hyp_id)
    final = asyncio.run(graph.ainvoke(initial_state(charter, hypothesis, design), {"recursion_limit": 200}))

    assert final["rejections"][0].kind == "guarantee_violation"
    assert "conclude" in final["rejections"][0].detail


def test_retry_cannot_smuggle_a_forbidden_action_through(loop_db_session):
    """The whole safety argument for retry: the schema is rebuilt
    IDENTICALLY each attempt, so a retry is a second chance to pick
    something legal, never a second chance at the forbidden thing.
    """
    state = _state_for_schema()
    from agentic_core.loop_state import build_decision_model

    first = build_decision_model(state).model_json_schema()
    second = build_decision_model(state).model_json_schema()
    assert first == second
    assert "conclude" not in str(first["$defs"].keys())


def _state_for_schema():
    from tests.agentic_core.test_loop_state import _evidence_complete, _state

    return _state(n_windows=5, results=_evidence_complete())


# ---------------------------------------------------------------------------
# Prompt compaction
# ---------------------------------------------------------------------------


def test_compact_collapses_series_but_keeps_scalar_metrics():
    """The metrics a verdict is built from must survive compaction intact;
    only the bulk series collapse. If sharpe_ratio ever got summarized
    away, the agent would be deciding blind.
    """
    from agentic_core.loop_graph import _compact

    out = _compact({
        "sharpe_ratio": 1.12,
        "num_trades": 42,
        "trade_returns": [0.01] * 400,
    })
    assert out["sharpe_ratio"] == 1.12
    assert out["num_trades"] == 42
    assert isinstance(out["trade_returns"], str)
    assert "400 items" in out["trade_returns"]


def test_compaction_does_not_touch_the_stored_trace(loop_db_session):
    """The whole safety argument for compaction is that it applies to the
    PROMPT only -- Component 7 validates claims against the trace table,
    so the stored payload must stay complete. If this ever fails,
    compaction has leaked into the durable record and the fabrication
    check is reading truncated evidence.
    """
    hyp_id, design_id, charter, hypothesis, design = _seed(loop_db_session, 2)

    class BigSession(FakeSession):
        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return FakeToolResponse({"sharpe_ratio": 1.1, "trade_returns": [0.01] * 500})

    fake = BigSession()
    graph = build_graph(lambda: fake, LazyAgent(), design_id=design_id, hypothesis_id=hyp_id)
    final = asyncio.run(graph.ainvoke(initial_state(charter, hypothesis, design), {"recursion_limit": 200}))

    traces = loop_db_session.query(ToolCallTraceRow).filter_by(study_run_id=final["study_run_id"]).all()
    assert traces
    for t in traces:
        assert len(t.result["trade_returns"]) == 500


def test_a_permanently_failing_control_can_never_reach_completed(loop_db_session):
    """The inverse of the guarantee: if the control cannot succeed, there
    is no path to a verdict at all.
    """
    final, _, _, hyp_id = _run(loop_db_session, n_windows=2, fail_tools={"test_significance"})
    assert final["status"] != "completed"
    assert loop_db_session.query(StudyRunRow).filter_by(status="completed").count() == 0
