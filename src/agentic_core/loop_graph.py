"""The LangGraph execution loop -- Stage 5, Component 6a. See
docs/explanations/stage-5/step-07-execution-loop-state.md for the full
design reasoning.

This module is machinery. Every guarantee it enforces is defined in
loop_state.py; the nodes here only route between them. The one thing that
lives here and nowhere else is WINDOW INJECTION: execute_tool builds the
tool arguments, and it takes the date window from state, never from the
LLM's action. That is the line lookahead cannot cross.

Both the MCP session and the LLM are injected into build_graph rather than
imported at module level. That is what makes Component 6a testable with no
network, no subprocess, and no Bedrock spend: a fake session returning
canned results and a hostile fake LLM exercise the whole graph
deterministically. The real ones get wired in Component 6b.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.loop_state import (
    INDICATOR_TOOLS,
    RULE_TOOLS,
    LoopState,
    ToolResult,
    build_decision_model,
    flatten_windows,
)
from agentic_core.schemas import Charter, Hypothesis, StudyDesign
from data_pipeline.db.session import SessionFactory

# The step/budget limit docs/architecture.md Step 4 requires ("a step/budget
# limit stops runaway loops (also serves as cost control)"). Counted on
# decide_next_action, NOT on execute_tool: the LLM call is what costs money,
# and a step that ends in advance_phase or a validation failure still spent
# one. A six-window walk-forward at ~4 calls per window is ~24, so 40 leaves
# real headroom for diagnostics without letting a stuck loop run forever.
MAX_STEPS = 40


class ToolSession(Protocol):
    """The slice of mcp.ClientSession the loop actually uses. Declared as a
    Protocol so a test fake satisfies it structurally without importing or
    subclassing anything from the MCP SDK.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


LLMCallable = Callable[..., BaseModel]


def _window_summary(state: LoopState, window_index: int) -> str:
    lines = []
    for r in state["results"]:
        if r.window_index != window_index:
            continue
        if r.is_error:
            lines.append(f"  - {r.tool_name}: ERROR {r.result.get('error', '')}")
        else:
            lines.append(f"  - {r.tool_name}: {r.result}")
    return "\n".join(lines) or "  (nothing run yet in this window)"


def _decide_prompt(state: LoopState) -> str:
    """Full results for the current window, compact summaries for completed
    ones.

    The trimming is safe specifically because Component 7 validates every
    verdict claim against the tool_call_traces TABLE, not against whatever
    was in this prompt -- so shrinking the prompt cannot weaken the
    fabrication check. Without that property this would be a dangerous
    optimization; with it, it is just cost control on a prompt that would
    otherwise grow with every step.
    """
    windows = state["windows"]
    w = state["window_index"]
    current = windows[w]
    hyp = state["hypothesis"].parsed
    fc = hyp.falsification_condition

    prior_blocks = []
    for i in range(w):
        done = {r.tool_name for r in state["results"] if r.window_index == i and not r.is_error}
        prior_blocks.append(f"  window {i} ({windows[i].start}..{windows[i].end}): ran {sorted(done)}")
    prior = "\n".join(prior_blocks) or "  (none -- this is the first window)"

    return f"""You are executing a pre-registered study. Decide the single next action.

Hypothesis: {hyp.prediction}
Rule: {hyp.rule.name}
Pre-registered falsification: this hypothesis FAILS if {fc.metric} is {fc.comparison} {fc.threshold}
Null hypothesis being tested: {state["design"].null_hypothesis}

You are in window {w} of {len(windows)} ({current.start} .. {current.end}).
{"This is the IN-SAMPLE window." if w == 0 else "This is an OUT-OF-SAMPLE window."}

Completed windows:
{prior}

Results in the current window:
{_window_summary(state, w)}

Steps used: {state["step_count"]} of {MAX_STEPS}.

You do not choose date ranges -- the window above is applied automatically
to every tool call. You do not choose the rule; it is frozen from
pre-registration. Choose only which tool to run next, on which ticker.

Only the actions offered in the response schema are available to you right
now. If an action you expect is missing, its precondition is not yet met.
"""


def _tool_arguments(state: LoopState, action: BaseModel) -> dict[str, Any]:
    """THE lookahead boundary.

    start/end come from state["windows"][window_index] and nowhere else.
    The action object physically has no date field to read (see
    loop_state.build_decision_model), so this is not "ignoring" an
    LLM-supplied date -- there is no such value in existence to ignore.
    The same holds for `rule`: frozen at pre-registration, injected here.
    """
    window = state["windows"][state["window_index"]]
    args: dict[str, Any] = {
        "ticker": action.ticker,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
    }
    if action.tool in RULE_TOOLS:
        args["rule"] = state["hypothesis"].parsed.rule.model_dump(mode="json")
    if action.tool in INDICATOR_TOOLS:
        args["name"] = action.indicator
    return args


def make_initialize(design_id: str, hypothesis_id: str):
    def initialize(state: LoopState) -> dict[str, Any]:
        """Creates the StudyRun row and flips the hypothesis to 'testing'.
        Component 7 owns every later status transition (confirmed /
        rejected / inconclusive); this node deliberately does not set an
        outcome, only 'work has started'.
        """
        with SessionFactory() as session:
            session.add(
                StudyRunRow(
                    id=state["study_run_id"],
                    hypothesis_id=hypothesis_id,
                    study_design_id=design_id,
                    status="running",
                    step_count=0,
                    started_at=datetime.now(),
                )
            )
            hyp_row = session.get(HypothesisRow, hypothesis_id)
            hyp_row.status = "testing"
            session.commit()
        return {"windows": flatten_windows(state["design"]), "status": "running"}

    return initialize


def make_decide_next_action(llm: LLMCallable):
    def decide_next_action(state: LoopState) -> dict[str, Any]:
        """The only node that calls an LLM.

        The response model is rebuilt from the CURRENT state every step, so
        availability is a property of the schema at this instant rather
        than a rule applied afterwards.
        """
        model = build_decision_model(state)
        decision = llm(_decide_prompt(state), response_model=model)
        return {
            "pending_action": decision.decision,
            "step_count": state["step_count"] + 1,
        }

    return decide_next_action


def make_execute_tool(session_provider: Callable[[], ToolSession]):
    async def execute_tool(state: LoopState) -> dict[str, Any]:
        action = state["pending_action"]
        args = _tool_arguments(state, action)
        step_index = state["step_count"]
        window_index = state["window_index"]

        response = await session_provider().call_tool(action.tool, args)
        is_error = bool(getattr(response, "is_error", False))
        payload = getattr(response, "structured_content", None) or {}

        result = ToolResult(
            step_index=step_index,
            window_index=window_index,
            tool_name=action.tool,
            arguments=args,
            result=payload,
            is_error=is_error,
        )

        # Written synchronously, before the next decision is made. These
        # rows -- not LangGraph's own state -- are the durable record
        # Component 7 validates claims against and a UI would poll, which
        # is why no checkpointer is configured (see build_graph).
        with SessionFactory() as db:
            db.add(
                ToolCallTraceRow(
                    study_run_id=state["study_run_id"],
                    step_index=step_index,
                    window_index=window_index,
                    tool_name=action.tool,
                    arguments=args,
                    result=payload,
                    is_error=is_error,
                    called_at=datetime.now(),
                )
            )
            db.commit()

        return {"results": [result], "pending_action": None}

    return execute_tool


def advance_phase(state: LoopState) -> dict[str, Any]:
    """Deterministic, and its own node rather than a flag inside
    execute_tool -- so the phase transition is visible in the graph's own
    execution path and the guard is evaluated on the edge into it.
    """
    return {"window_index": state["window_index"] + 1, "pending_action": None}


def make_finalize(status: str | None = None):
    def finalize(state: LoopState) -> dict[str, Any]:
        final_status = status or state["status"]
        with SessionFactory() as session:
            row = session.get(StudyRunRow, state["study_run_id"])
            row.status = final_status
            row.step_count = state["step_count"]
            row.finished_at = datetime.now()
            session.commit()
        return {"status": final_status}

    return finalize


def route_after_decide(state: LoopState) -> str:
    """Budget is checked HERE, in code, on the edge -- never by asking the
    LLM to stop. Exhaustion routes to failure, never to a verdict: a study
    that ran out of budget mid-walk-forward has untested windows, and a
    confident conclusion drawn from partial folds is exactly the dishonest
    output this project exists to prevent.
    """
    if state["step_count"] > MAX_STEPS:
        return "budget_exhausted"
    action = state["pending_action"]
    return {"call_tool": "execute", "advance_phase": "advance", "conclude": "conclude"}[action.action]


def build_graph(session_provider: Callable[[], ToolSession], llm: LLMCallable, *, design_id: str, hypothesis_id: str):
    """No checkpointer is configured, deliberately.

    study_runs and tool_call_traces are written synchronously as the loop
    runs, so the durable record already exists in Postgres. Adding
    LangGraph's own checkpointer would create a second, overlapping source
    of truth for the same state, able to disagree with the first after a
    partial failure. The cost of this choice, stated plainly: a crashed run
    is NOT resumable mid-step -- it is re-run from scratch or left
    'failed'. That is acceptable for Stage 5, whose gate is about honesty
    and killing hypotheses rather than durability under crash. Stage 8's
    scheduled overnight runs are where crash-resume becomes a real
    requirement, and a checkpointer can be added there without
    restructuring this graph.
    """
    builder = StateGraph(LoopState)

    builder.add_node("initialize", make_initialize(design_id, hypothesis_id))
    builder.add_node("decide", make_decide_next_action(llm))
    builder.add_node("execute", make_execute_tool(session_provider))
    builder.add_node("advance", advance_phase)
    builder.add_node("conclude", make_finalize("completed"))
    builder.add_node("budget_exhausted", make_finalize("failed"))

    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "decide")
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "execute": "execute",
            "advance": "advance",
            "conclude": "conclude",
            "budget_exhausted": "budget_exhausted",
        },
    )
    builder.add_edge("execute", "decide")
    builder.add_edge("advance", "decide")
    builder.add_edge("conclude", END)
    builder.add_edge("budget_exhausted", END)

    return builder.compile()


def initial_state(
    charter: Charter,
    hypothesis: Hypothesis,
    design: StudyDesign,
    study_run_id: str | None = None,
) -> LoopState:
    return LoopState(
        study_run_id=study_run_id or str(uuid.uuid4()),
        charter=charter,
        hypothesis=hypothesis,
        design=design,
        windows=[],
        window_index=0,
        step_count=0,
        pending_action=None,
        results=[],
        status="running",
        failure_reason=None,
    )
