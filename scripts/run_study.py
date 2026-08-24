"""Stage 5, Component 6b -- the live execution loop.

Swaps Component 6a's fake session and fake LLM for the real ones: a real
MCP server subprocess over stdio (the same launch path Stage 4's gate
script proved in scripts/verify_stage4_gate.py) and real Claude via
llm_client.structured_output. The graph itself is unchanged -- both were
injected parameters from the start, which is the entire payoff of the
6a/6b split.

Usage:
    python -m scripts.run_study <study_design_id>

Prints a step-accounting summary at the end. MAX_STEPS was set at 40 from
a reasoned estimate (six windows x ~4 calls, plus headroom) with no real
run behind it; this script exists partly to replace that estimate with an
observed number, the same way Stage 4 revised n_resamples once real
timing data existed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters, stdio_client

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.hypothesis import hypothesis_from_row
from agentic_core.loop_graph import MAX_STEPS, build_graph, initial_state
from agentic_core.schemas import Charter, StudyDesign
from data_pipeline.db.session import SessionFactory
from llm_client import StructuredOutputError, structured_output


def _load(study_design_id: str):
    with SessionFactory() as session:
        design_row = session.get(StudyDesignRow, study_design_id)
        if design_row is None:
            raise SystemExit(f"no study_design with id {study_design_id!r}")
        hyp_row = session.get(HypothesisRow, design_row.hypothesis_id)
        charter_row = session.get(CharterRow, hyp_row.charter_id)
        return (
            Charter.model_validate(charter_row.charter),
            hypothesis_from_row(hyp_row),
            StudyDesign.model_validate(design_row.design),
            hyp_row.id,
        )


class TracingLLM:
    """Wraps structured_output to record what the model chose at each step.

    Deliberately a wrapper rather than an edit to decide_next_action: the
    loop's own behavior must be identical whether or not anyone is
    watching it, so observation lives outside the graph.
    """

    def __init__(self):
        self.decisions: list[tuple[str, str, float]] = []

    def __call__(self, prompt: str, response_model):
        started = time.monotonic()
        try:
            result = structured_output(prompt, response_model=response_model)
        except StructuredOutputError as e:
            # Print exactly what Claude emitted before re-raising. Without
            # this, a schema-shape failure surfaces only as Pydantic's
            # truncated repr of the offending value, which is not enough to
            # tell a malformed payload from a mis-specified schema.
            print(f"  step {len(self.decisions) + 1:>2}  LLM OUTPUT REJECTED -- raw tool_use input:")
            for block in e.raw_response.content:
                if block.type == "tool_use":
                    print(f"    {block.input!r}")
            raise
        elapsed = time.monotonic() - started
        d = result.decision
        label = getattr(d, "tool", d.action)
        self.decisions.append((d.action, label, elapsed))
        print(f"  step {len(self.decisions):>2}  {d.action:<14} {label:<20} ({elapsed:.1f}s)")
        return result


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.run_study <study_design_id>")
    design_id = sys.argv[1]
    charter, hypothesis, design, hypothesis_id = _load(design_id)

    n_windows = len(design.walk_forward_windows or [design.in_sample, design.out_of_sample])
    print(f"Study design : {design_id}")
    print(f"Hypothesis   : {hypothesis.parsed.rule.name}")
    print(f"Design type  : {design.parsed.design_type} ({n_windows} windows)")
    print(f"Universe     : {charter.resolved_universe}")
    print(f"Budget       : MAX_STEPS = {MAX_STEPS}")
    print()

    params = StdioServerParameters(
        command=os.path.abspath(".venv/bin/python3"),
        args=["-m", "mcp_tools.server"],
        cwd=os.getcwd(),
    )

    llm = TracingLLM()
    wall_start = time.monotonic()

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            graph = build_graph(
                lambda: session, llm, design_id=design_id, hypothesis_id=hypothesis_id
            )
            final = await graph.ainvoke(initial_state(charter, hypothesis, design))

    wall = time.monotonic() - wall_start
    used = final["step_count"]
    llm_time = sum(e for _, _, e in llm.decisions)

    print()
    print("=" * 68)
    print(f"status            : {final['status']}")
    print(f"study_run_id      : {final['study_run_id']}")
    print(f"windows visited   : {final['window_index'] + 1} of {n_windows}")
    print(f"tool calls traced : {len(final['results'])}")
    print()
    print("--- STEP ACCOUNTING (MAX_STEPS calibration) ---")
    print(f"steps used        : {used}")
    print(f"budget            : {MAX_STEPS}")
    print(f"headroom          : {MAX_STEPS - used} steps ({used / MAX_STEPS:.0%} of budget used)")
    print(f"per-window average: {used / (final['window_index'] + 1):.1f} steps")
    print()
    print(f"wall clock        : {wall:.1f}s   (LLM {llm_time:.1f}s, tools {wall - llm_time:.1f}s)")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
