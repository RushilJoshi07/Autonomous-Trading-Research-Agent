"""Stage 5, Component 7 -- render the verdict for a completed study run.

Usage:
    python -m scripts.render_verdict <study_run_id>

Prints the code-decided status and every gate BEFORE the LLM is called, so
the mechanical decision is visible independently of the prose written
around it. That ordering is the point: if the printed status and the
printed narrative ever disagree, the status is what the evidence says.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.hypothesis import hypothesis_from_row
from agentic_core.loop_state import flatten_windows
from agentic_core.schemas import StudyDesign
from agentic_core.verdict import (
    corrected_threshold,
    decide_status,
    evaluate_windows,
    render_verdict,
)
from data_pipeline.db.session import SessionFactory


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.render_verdict <study_run_id>")
    study_run_id = sys.argv[1]

    with SessionFactory() as s:
        run = s.get(StudyRunRow, study_run_id)
        hyp = hypothesis_from_row(s.get(HypothesisRow, run.hypothesis_id))
        design = StudyDesign.model_validate(s.get(StudyDesignRow, run.study_design_id).design)
        traces = list(s.execute(
            select(ToolCallTraceRow).where(ToolCallTraceRow.study_run_id == study_run_id)
            .order_by(ToolCallTraceRow.step_index)
        ).scalars())
        n_hyp = len(s.execute(
            select(HypothesisRow.id).where(HypothesisRow.charter_id == s.get(HypothesisRow, run.hypothesis_id).charter_id)
        ).all())

    fc = hyp.parsed.falsification_condition
    evals = evaluate_windows(traces, len(flatten_windows(design)), fc.metric)
    threshold = corrected_threshold(n_hyp, hyp.grounding_tier)
    status, gates = decide_status(evals, fc, threshold)

    print(f"Study run   : {study_run_id}")
    print(f"Hypothesis  : {hyp.parsed.rule.name}  (grounding: {hyp.grounding_tier})")
    print(f"Pre-registered: FAILS if {fc.metric} {fc.comparison} {fc.threshold}")
    print(f"Threshold   : p < {threshold:.4f}  ({n_hyp} hypothesis/es under charter)")
    print()
    print("Evidence:")
    for e in evals:
        tag = "IN-SAMPLE " if not e.is_out_of_sample else "out-of-sample"
        print(f"  win {e.window_index} {tag}  {fc.metric}={e.metric_value}  trades={e.num_trades}  p={e.p_value}")
    print()
    print("--- MECHANICAL DECISION (before any LLM call) ---")
    for g in gates:
        print(f"  [{'PASS' if g.passed else 'FAIL'}] {g.name}: {g.detail}")
    print(f"  => STATUS: {status.upper()}")
    print()

    verdict_id, verdict = render_verdict(study_run_id)

    print("--- VERDICT WRITTEN ---")
    print(f"verdict_id : {verdict_id}")
    print(f"status     : {verdict.status}")
    print()
    print("narrative:")
    print(f"  {verdict.parsed.narrative}")
    print()
    print(f"claims ({len(verdict.parsed.claims)}), each validated against a real trace:")
    for c in verdict.parsed.claims:
        print(f"  - {c.metric}={c.value} (trace {c.tool_call_trace_id}): {c.statement}")
    print()
    print("caveats:")
    for c in verdict.caveats:
        print(f"  - {c}")


if __name__ == "__main__":
    main()
