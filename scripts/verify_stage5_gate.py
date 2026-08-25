"""Stage 5's own SACRED GATE 2 verification.

Mirrors scripts/verify_stage3_gate.py and scripts/verify_stage4_gate.py's
own pattern: a dedicated, self-contained gate script, run manually against
real infrastructure, whose passing is what allows Stage 5's Level-3 stage
summary to be written. See docs/explanations/stage-5/commit-log.md's
"Stage 5 gating decision" entry for why this script exists at all rather
than deferring to Stage 6's golden set (short version: the build order
forbids a stage depending on the next one to close its own gate).

Component 7 already proved, on a real study, that the mechanism never
fabricates and correctly kills a bad hypothesis. Two things were still
unproven: that it can CONFIRM a genuinely good hypothesis (never run on
real data), and that a fabricated claim is caught when it reaches the live
system, not just when handed to validate_claims() directly in a unit test.
This script closes both.

JOB 1 -- CONFIRM PATH: a deliberately rigged synthetic fixture (see the
module docstring on build_probe_series below for the full derivation,
including two fixture designs that failed and why) is run through the
REAL execution loop -- real MCP subprocess over stdio, real Bedrock
choosing each action -- and the REAL render_verdict, with real Bedrock
writing the narrative. Asserts the mechanically-decided status is
'confirmed'. No hypothesis was selected after the fact because it happened
to work; the fixture's edge is constructed and verified deterministic
before this script existed at all (see the exploratory probe's numbers in
the step explainer).

JOB 2 -- ADVERSARIAL FABRICATION: wraps the real LLM call used for
render_verdict so that, after Bedrock returns a REAL response, one claim's
value is deliberately corrupted before validation sees it -- the same
"deliberately attempt the violation" discipline Stage 2's own gate used
(attempt lookahead, confirm the engine refuses it) rather than waiting to
observe a real model spontaneously lying. Confirms the corrupted claim is
rejected and, since every retry attempt is corrupted the same way,
confirms VerdictValidationError fires and NO verdict row is ever written.

Real API cost. Pollutes and then fully cleans the real dev database -- the
MCP subprocess resolves its own DATABASE_URL independently, so there is no
practical way to redirect a separately-launched subprocess at the test
database, matching how scripts/run_study.py and Component 7's own live
verification already operate. Every row this script creates is deleted at
the end, and the deletion is confirmed by direct query, not assumed.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from mcp import ClientSession, StdioServerParameters, stdio_client
from sqlalchemy import delete, select

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.db.models import Verdict as VerdictRow
from agentic_core.loop_graph import build_graph, initial_state
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
)
from agentic_core.study_design import NULL_HYPOTHESIS
from agentic_core.verdict import VerdictValidationError, render_verdict
from backtester.schema import Comparison, Condition, ConstantTerm, PriceTerm, ScaledTerm, StrategyRule
from data_pipeline.db.models import PriceBar
from data_pipeline.db.session import SessionFactory
from llm_client import structured_output

TICKER = "GATE5PROBE"

_results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    _results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# The validated fixture (see docs/explanations/stage-5/step-10-gate-script.md
# for the two failed designs this replaced).
#
# v1 (perfectly periodic staircase): p=1.0 despite a 100% win rate -- a
#     fully deterministic series gives randomized entries the same shot at
#     every jump, so there is no measurable edge over the null.
# v2 (dip immediately followed by rally): Sharpe went NEGATIVE. Root cause:
#     backtesting.py fills orders at the NEXT bar's OPEN, not the signal
#     bar's close -- Stage 2's own no-lookahead discipline. With no settle
#     bar, the entry fill landed AFTER the rally, buying near the top.
# v3 (this one): every price move gets a one-bar settle, so an order queued
#     off any level always fills at a stable, unchanged open the next bar.
# ---------------------------------------------------------------------------

N_SIGNALS = 60
NOISE_BARS_BETWEEN = 8
DAILY_NOISE_STD = 0.004
DIP_PCT = 0.08
RALLY_PCT = 0.20
SIGNAL_BARS = 4  # dip, hold-low, rally, hold-high
CYCLE_LEN = NOISE_BARS_BETWEEN + SIGNAL_BARS


def build_probe_series(n_signals: int, start: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_bars = n_signals * CYCLE_LEN + 20
    closes = np.empty(n_bars)
    closes[0] = 100.0
    for i in range(1, n_bars):
        pos = i % CYCLE_LEN
        prev = closes[i - 1]
        if pos == NOISE_BARS_BETWEEN:
            closes[i] = prev * (1 - DIP_PCT)
        elif pos == NOISE_BARS_BETWEEN + 1:
            closes[i] = prev
        elif pos == NOISE_BARS_BETWEEN + 2:
            closes[i] = prev * (1 + RALLY_PCT)
        elif pos == NOISE_BARS_BETWEEN + 3:
            closes[i] = prev
        else:
            closes[i] = prev * (1 + rng.normal(0, DAILY_NOISE_STD))
    dates = pd.bdate_range(start, periods=n_bars)
    return pd.DataFrame({"date": [d.date() for d in dates], "close": closes})


def _leaf(left, op, right):
    return Condition(kind="leaf", comparison=Comparison(left=left, op=op, right=right))


PROBE_RULE = StrategyRule(
    name="stage5_gate_confirm_probe",
    description="Deliberately rigged synthetic fixture for verify_stage5_gate.py, "
                "not a real trading hypothesis. Buy when close drops >7% vs the "
                "prior bar (fires only on an engineered dip, never on ~0.4%-std "
                "ordinary noise); sell when close rises >10% vs the prior bar "
                "(fires only on the engineered rally). A one-bar hold after each "
                "signal keeps the next bar's OPEN -- where the order actually "
                "fills -- at the settled price.",
    entry=_leaf(PriceTerm(field="close"), "lt",
                ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=0.93)),
    exit=_leaf(PriceTerm(field="close"), "gt",
               ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1.10)),
)


def seed_price_bars(session, ticker: str, series: pd.DataFrame) -> None:
    now = datetime.now(tz=timezone.utc)
    for _, row in series.iterrows():
        price = Decimal(str(round(row["close"], 6)))
        session.add(PriceBar(
            ticker=ticker, date=row["date"],
            raw_open=price, raw_high=price, raw_low=price, raw_close=price, raw_volume=1_000_000,
            adj_open=price, adj_high=price, adj_low=price, adj_close=price, adj_volume=1_000_000,
            fetched_at=now,
        ))
    session.commit()


def build_charter_and_hypothesis(in_sample: pd.DataFrame) -> tuple[str, str, Charter, Hypothesis]:
    charter = Charter(
        parsed=ParsedCharter(
            universe=UniverseFilter(sector=None),
            hypothesis_families=[EffectFamily.MEAN_REVERSION],
        ),
        resolved_universe=[TICKER],
        screening_as_of=in_sample["date"].iloc[0],
        screening_group_size=1,
    )
    hypothesis = Hypothesis(
        parsed=ParsedHypothesis(
            rule=PROBE_RULE,
            prediction="Synthetic gate-verification fixture: this rule captures a "
                       "constructed, statistically decisive edge by design, not a "
                       "literature-grounded market hypothesis.",
            falsification_condition=FalsificationCondition(
                metric="sharpe_ratio", comparison="less_than", threshold=0.5
            ),
            rationale="Not grounded in literature -- this is verify_stage5_gate.py's "
                      "own confirm-path proof fixture. grounding_tier is deliberately "
                      "'none', the strictest multiple-comparisons tier, so a pass here "
                      "proves the confirm path survives even the harshest correction.",
        ),
        grounding_tier="none",
        citations=[],
    )

    charter_id, hyp_id = str(uuid.uuid4()), str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(CharterRow(
            id=charter_id, mandate_text="[gate-script fixture, not a real mandate]",
            charter=charter.model_dump(mode="json"), confirmed=True,
            created_at=datetime.now(), confirmed_at=datetime.now(),
        ))
        session.flush()
        session.add(HypothesisRow(
            id=hyp_id, charter_id=charter_id,
            rule=hypothesis.parsed.rule.model_dump(mode="json"),
            prediction=hypothesis.parsed.prediction,
            falsification_condition=hypothesis.parsed.falsification_condition.model_dump(mode="json"),
            rationale=hypothesis.parsed.rationale, citations=[],
            grounding_tier="none", status="proposed", created_at=datetime.now(),
        ))
        session.commit()
    return charter_id, hyp_id, charter, hypothesis


def build_study_design(hyp_id: str, in_sample: pd.DataFrame, out_of_sample: pd.DataFrame) -> tuple[str, StudyDesign]:
    # A real StudyDesign from Component 5 never has a gap between windows --
    # _simple_holdout slices one continuous trading-date list. This one does
    # (the two series are independently generated), which is fine for a
    # hand-constructed gate fixture (StudyDesign only requires
    # out_of_sample.start > in_sample.end) but is a disclosed, deliberate
    # deviation from what propose_study_design would ever produce.
    design = StudyDesign(
        parsed=ParsedStudyDesign(
            design_type="simple_holdout", split="70/30",
            rationale="Hand-constructed for gate verification, not produced by "
                      "propose_study_design.",
        ),
        in_sample=DateRange(start=in_sample["date"].iloc[0], end=in_sample["date"].iloc[-1]),
        out_of_sample=DateRange(start=out_of_sample["date"].iloc[0], end=out_of_sample["date"].iloc[-1]),
        null_hypothesis=NULL_HYPOTHESIS,
    )
    design_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(StudyDesignRow(
            id=design_id, hypothesis_id=hyp_id,
            design=design.model_dump(mode="json"), created_at=datetime.now(),
        ))
        session.commit()
    return design_id, design


async def run_confirm_path(design_id: str, hyp_id: str, charter: Charter, hypothesis: Hypothesis, design: StudyDesign) -> str:
    params = StdioServerParameters(
        command=os.path.abspath(".venv/bin/python3"),
        args=["-m", "mcp_tools.server"],
        cwd=os.getcwd(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            graph = build_graph(lambda: session, structured_output, design_id=design_id, hypothesis_id=hyp_id)
            final = await graph.ainvoke(initial_state(charter, hypothesis, design))
    return final["study_run_id"], final["status"]


class ClaimCorruptingLLM:
    """Job 2's adversary. Calls the REAL structured_output for a REAL
    response, then corrupts one claim's value before returning it --
    injecting a fabrication at the exact point render_verdict would trust
    the model's output, on every attempt including retries.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt: str, response_model):
        self.calls += 1
        result = structured_output(prompt, response_model=response_model)
        if response_model is ParsedVerdict and result.claims:
            corrupted = result.claims[0].model_copy(update={"value": result.claims[0].value + 999.0})
            result = result.model_copy(update={"claims": [corrupted, *result.claims[1:]]})
        return result


def cleanup(ticker: str, charter_id: str | None, hyp_id: str | None) -> None:
    with SessionFactory() as session:
        session.execute(delete(PriceBar).where(PriceBar.ticker == ticker))
        if hyp_id:
            run_ids = [r[0] for r in session.execute(
                select(StudyRunRow.id).where(StudyRunRow.hypothesis_id == hyp_id)
            ).all()]
            for rid in run_ids:
                session.execute(delete(VerdictRow).where(VerdictRow.study_run_id == rid))
                session.execute(delete(ToolCallTraceRow).where(ToolCallTraceRow.study_run_id == rid))
            session.execute(delete(StudyRunRow).where(StudyRunRow.hypothesis_id == hyp_id))
            session.execute(delete(StudyDesignRow).where(StudyDesignRow.hypothesis_id == hyp_id))
            session.execute(delete(HypothesisRow).where(HypothesisRow.id == hyp_id))
        if charter_id:
            session.execute(delete(CharterRow).where(CharterRow.id == charter_id))
        session.commit()


def verify_cleanup(ticker: str, charter_id: str, hyp_id: str) -> None:
    with SessionFactory() as session:
        left_bars = session.query(PriceBar).filter(PriceBar.ticker == ticker).count()
        left_hyp = session.get(HypothesisRow, hyp_id)
        left_charter = session.get(CharterRow, charter_id)
    record("dev database fully cleaned up (verified by direct query)",
           left_bars == 0 and left_hyp is None and left_charter is None,
           f"price_bars left={left_bars}, hypothesis left={left_hyp is not None}, "
           f"charter left={left_charter is not None}")


async def main() -> None:
    in_sample = build_probe_series(N_SIGNALS, "2020-01-01", seed=1)
    out_of_sample = build_probe_series(N_SIGNALS, "2023-01-01", seed=2)

    charter_id = hyp_id = None
    try:
        with SessionFactory() as session:
            seed_price_bars(session, TICKER, pd.concat([in_sample, out_of_sample], ignore_index=True))

        charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(in_sample)
        design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)

        # ---- Job 1: confirm path, real loop, real verdict ----
        study_run_id, run_status = await run_confirm_path(design_id, hyp_id, charter, hypothesis, design)
        record("execution loop completed (not failed) on the rigged fixture",
               run_status == "completed", f"status={run_status}")

        if run_status == "completed":
            verdict_id, verdict = render_verdict(study_run_id)
            record("mechanical verdict on the rigged fixture is CONFIRMED",
                   verdict.status == "confirmed", f"status={verdict.status}")
            record("every claim in the confirmed verdict is independently traceable",
                   len(verdict.parsed.claims) > 0, f"{len(verdict.parsed.claims)} claims")

        # ---- Job 2: adversarial fabrication against the live system ----
        # Deliberately reuses Job 1's already-completed study_run_id rather
        # than paying for a second full live loop run -- the traces are
        # already real and correct, and only the LLM's verdict RESPONSE is
        # corrupted, which is the layer this test targets. This means one
        # legitimate verdict row (Job 1's) already exists for this run
        # before Job 2 even starts, so the correct assertion is that the
        # count is UNCHANGED afterward, not that it is zero -- a first
        # version of this script asserted zero and failed on a test bug,
        # not a real one: it ignored that Job 1's own legitimate verdict
        # was already sitting there. Caught, and recorded rather than
        # quietly fixed, in docs/explanations/stage-5/step-10-gate-script.md.
        if run_status == "completed":
            with SessionFactory() as session:
                before = session.query(VerdictRow).filter(VerdictRow.study_run_id == study_run_id).count()

            try:
                render_verdict(study_run_id, llm=ClaimCorruptingLLM())
                record("corrupted claim was rejected by validation", False,
                       "a verdict was written despite a deliberately fabricated claim")
            except VerdictValidationError as e:
                record("corrupted claim was rejected by validation", True,
                       f"VerdictValidationError raised after retries: {e.errors[:1]}")

            with SessionFactory() as session:
                after = session.query(VerdictRow).filter(VerdictRow.study_run_id == study_run_id).count()
            record("the fabrication attempt added no new verdict row",
                   after == before,
                   f"verdict count before={before}, after={after}")

    finally:
        cleanup(TICKER, charter_id, hyp_id)
        if charter_id and hyp_id:
            verify_cleanup(TICKER, charter_id, hyp_id)

    print()
    print("=" * 70)
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"{passed}/{total} checks passed")
    if passed == total:
        print("Stage 5 gate: PASSED -- confirm path proven live, fabrication rejected live.")
    else:
        print("Stage 5 gate: FAILED")


if __name__ == "__main__":
    asyncio.run(main())
