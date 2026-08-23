"""The execution loop's state, tool tiers, and gating rules -- Stage 5,
Component 6a. See docs/explanations/stage-5/step-07-execution-loop-state.md
for the full design reasoning.

This module holds every structural guarantee the loop makes. The graph in
loop_graph.py is machinery; the guarantees live here, and they are all
enforced the same way: by what the LLM's response schema does not contain.

Three guarantees, in order of importance:

1. NO LOOKAHEAD. The action model has no start/end/rule fields at all --
   not "validated", not "checked", ABSENT. execute_tool supplies the date
   window from state, so there is no value the LLM could emit that would
   reach a tool as a date. This is the same structural move Component 5
   made by leaving control_required off StudyDesign: an option that does
   not exist cannot be chosen.

2. EVIDENCE BEFORE DIAGNOSIS. Diagnostic tools are absent from the tool
   enum until the current window holds a successful evidence result. The
   risk this closes is not lookahead (guarantee 1 already closed that) but
   ORDER: an agent that inspects raw data before running the backtest
   arrives at the number with a narrative already written, and then
   interprets the number against it.

3. NO WINDOW LEFT UNTESTED. A window is left the same way whether by
   advancing or concluding -- both require a successful run_backtest AND
   test_significance in that window. AdvancePhase and Conclude are absent
   from the union when their guard is unmet, so a walk-forward study
   cannot be silently truncated to its first fold. Budget exhaustion
   (loop_graph.MAX_STEPS) is the only escape, and it produces
   status='failed', never a verdict.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from agentic_core.schemas import Charter, DateRange, Hypothesis, StudyDesign
from backtester.strategies.rule_strategy import rule_indicator_names

# The loop's tool vocabulary is deliberately NARROWER than the nine tools
# mcp_tools/server.py exposes -- a disclosed deviation from
# docs/architecture.md Step 4's tool list, confirmed with the user before
# it was built. Three MCP tools are excluded on purpose:
#
#   correct_p_values  -- takes p_values: list[float], i.e. NUMBERS THE LLM
#       TYPES. That is the model transcribing statistics by hand, and it
#       could transcribe selectively. The multiple-comparisons correction
#       belongs at verdict time (Component 7), driven by counting
#       hypotheses under the charter from the database.
#   screen_universe   -- would let the agent re-resolve the universe
#       mid-study, i.e. change the experiment's population after seeing
#       results.
#   list_indicators   -- pointless when the rule is frozen and injected;
#       the agent cannot change indicators, so listing them does nothing.
#
# EVIDENCE_TOOLS produce the numbers a verdict rests on. DIAGNOSTIC_TOOLS
# explain a result that already exists -- which is why they unlock only
# after one exists (guarantee 2 above).
EVIDENCE_TOOLS: tuple[str, ...] = ("run_backtest", "test_significance", "confidence_interval")
DIAGNOSTIC_TOOLS: tuple[str, ...] = ("classify_regime", "compute_indicator", "get_price_data")

# Tools whose MCP signature takes a `rule` argument. Injected from state,
# never from the LLM -- the pre-registered rule is frozen for the whole run.
RULE_TOOLS: frozenset[str] = frozenset({"run_backtest", "test_significance", "confidence_interval"})

# The only tool that needs an indicator name. Constrained to the rule's own
# indicators (see rule_indicator_names) rather than all ~190 registered
# ones: the diagnostic question is about THIS rule's behavior, and
# classify_regime already covers the general trend/volatility picture.
INDICATOR_TOOLS: frozenset[str] = frozenset({"compute_indicator"})


class ToolResult(BaseModel):
    """One recorded tool call. window_index is stamped here, not derived
    from dates later, so Component 7 can attribute a claim to the window it
    came from without reverse-engineering which DateRange the arguments
    matched.
    """

    step_index: int
    window_index: int
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    is_error: bool


class LoopState(TypedDict):
    """LangGraph's state object.

    `results` uses operator.add as its reducer, which means nodes can only
    ever APPEND to it. There is deliberately no reducer here that
    overwrites: no step can edit or erase evidence already recorded, which
    mirrors the append-only tool_call_traces table on the database side.
    Every other mutable field is a scalar a node replaces wholesale.
    """

    # written once at entry, never again
    study_run_id: str
    charter: Charter
    hypothesis: Hypothesis
    design: StudyDesign
    windows: list[DateRange]

    # mutable
    window_index: int
    step_count: int
    pending_action: Any | None
    results: Annotated[list[ToolResult], operator.add]
    status: Literal["running", "completed", "failed"]
    failure_reason: str | None


def flatten_windows(design: StudyDesign) -> list[DateRange]:
    """The ordered window list the loop walks.

    A simple_holdout is just a two-window study, so the loop never branches
    on design_type anywhere -- that uniformity is the direct payoff from
    Component 5 keeping in_sample/out_of_sample readable identically in
    both design types. walk_forward_windows already contains in_sample as
    its first element (see StudyDesign's docstring), so it is used whole,
    not concatenated with in_sample again.
    """
    if design.walk_forward_windows is not None:
        return list(design.walk_forward_windows)
    return [design.in_sample, design.out_of_sample]


def _successful_tools_in_window(state: LoopState, window_index: int) -> set[str]:
    """Only is_error=False results count. A tool that errored must not
    unlock anything -- otherwise "make the backtest fail" becomes a way
    through the evidence gate, which would invert the guard's whole point.
    """
    return {
        r.tool_name
        for r in state["results"]
        if r.window_index == window_index and not r.is_error
    }


def window_evidence_complete(state: LoopState, window_index: int) -> bool:
    """The bar for LEAVING a window, by either exit. Requires the mandatory
    control (test_significance -- docs/architecture.md's "did it beat
    randomized entries at the same trade frequency") to have actually run
    and succeeded, not merely to have been available.

    This is the second half of the mandatory-control guarantee. Component 5
    made skipping the control unrepresentable by giving StudyDesign no
    control_required field; this makes it unreachable by giving the loop no
    exit from a window that hasn't run it.
    """
    done = _successful_tools_in_window(state, window_index)
    return "run_backtest" in done and "test_significance" in done


def available_tools(state: LoopState) -> tuple[str, ...]:
    """Evidence tier always; diagnostic tier only once this window holds at
    least one successful evidence result.

    Note the asymmetry with window_evidence_complete: unlocking diagnostics
    needs ANY evidence tool to have succeeded, while leaving the window
    needs run_backtest AND test_significance specifically. Diagnosis is
    about having a real number to explain; leaving is about having run the
    full pre-registered comparison.
    """
    done = _successful_tools_in_window(state, state["window_index"])
    if done & set(EVIDENCE_TOOLS):
        return EVIDENCE_TOOLS + DIAGNOSTIC_TOOLS
    return EVIDENCE_TOOLS


def can_advance(state: LoopState) -> bool:
    """False on the last window -- there is nothing to advance to."""
    if state["window_index"] >= len(state["windows"]) - 1:
        return False
    return window_evidence_complete(state, state["window_index"])


def can_conclude(state: LoopState) -> bool:
    """Only from the last window, and only with that window's own evidence
    complete.

    The second condition is what stops the final fold from being reachable
    but evidentially skippable: without it, an agent could advance into the
    last window and conclude immediately, never testing it. Every window is
    left the same way.
    """
    return (
        state["window_index"] == len(state["windows"]) - 1
        and window_evidence_complete(state, state["window_index"])
    )


def build_decision_model(state: LoopState) -> type[BaseModel]:
    """Build the response model for ONE step of decide_next_action.

    Rebuilt every step on purpose. Availability is expressed as the
    membership of a typing.Literal, so a locked tool has no name the model
    can emit -- it is absent from the JSON schema sent to Bedrock, not
    rejected after the fact. This is the whole mechanism: everything the
    loop forbids is forbidden by omission from this schema.

    model_config = extra="forbid" everywhere matters more than it looks:
    without it, a model that invented a `start` field would have it
    silently dropped by Pydantic rather than raising, and the resulting
    action would look clean while the model's actual intent (peek at other
    dates) went unrecorded. Forbidding extras turns that into a loud
    validation failure.
    """
    tools = available_tools(state)
    tickers = tuple(state["charter"].resolved_universe)
    indicators = rule_indicator_names(state["hypothesis"].parsed.rule)

    # A rule built only from price/constant terms uses no indicators at all.
    # Literal[()] is not constructible, so rather than emit a broken schema,
    # compute_indicator is dropped from the vocabulary for such a rule --
    # there would be nothing legal to ask it for anyway.
    if not indicators:
        tools = tuple(t for t in tools if t not in INDICATOR_TOOLS)

    # Built with create_model rather than a class statement because two of
    # the field TYPES (tool, ticker) and the presence of a third
    # (indicator) are only known at runtime. create_model is Pydantic's
    # supported API for this; mutating model_fields on an already-declared
    # class also works today but is not a public contract, and this schema
    # is load-bearing enough that it should not rest on one.
    #
    # Note what is NOT among these fields: start, end, rule, commission,
    # cash, n_resamples, seed. Every one is supplied by execute_tool from
    # state. The date window in particular is the lookahead guarantee --
    # there is no field for a date, so no date can be chosen.
    call_tool_fields: dict[str, Any] = {
        "action": (Literal["call_tool"], ...),
        "tool": (Literal[tools], ...),
        "ticker": (Literal[tickers], ...),
        "reasoning": (str, Field(description="Why this call, given the results so far.")),
    }
    if indicators:
        # Declared conditionally so the field is absent entirely for a rule
        # with no indicators, rather than present-but-always-null.
        call_tool_fields["indicator"] = (
            Literal[indicators] | None,
            Field(default=None, description="Required for compute_indicator; the rule's own indicators only."),
        )

    CallTool = create_model(
        "CallTool",
        __config__=ConfigDict(extra="forbid"),
        **call_tool_fields,
    )

    class AdvancePhase(BaseModel):
        model_config = ConfigDict(extra="forbid")

        action: Literal["advance_phase"]
        reasoning: str

    class Conclude(BaseModel):
        model_config = ConfigDict(extra="forbid")

        action: Literal["conclude"]
        reasoning: str

    variants: list[type[BaseModel]] = [CallTool]
    if can_advance(state):
        variants.append(AdvancePhase)
    if can_conclude(state):
        variants.append(Conclude)

    if len(variants) == 1:
        decision_type: Any = CallTool
    else:
        decision_type = Annotated[Union[tuple(variants)], Field(discriminator="action")]

    class AgentDecision(BaseModel):
        """A wrapper, because llm_client.structured_output needs a single
        BaseModel to take model_json_schema() from -- a bare Union is not a
        model. The discriminator on `action` is what makes the union
        unambiguous to both Pydantic and the tool-use schema.
        """

        model_config = ConfigDict(extra="forbid")

        decision: decision_type  # type: ignore[valid-type]

    return AgentDecision
