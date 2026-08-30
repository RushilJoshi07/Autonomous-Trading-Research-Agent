"""Shared fixture-construction helpers for the Stage 6 golden set.

Deliberately NOT imported from or into scripts/verify_stage5_gate.py, even
though the logic here is a direct generalization of that script's own
build_probe_series / build_charter_and_hypothesis / build_study_design /
cleanup / verify_cleanup. That script's gate already PASSED, live, at real
API cost -- it is a closed historical proof, and editing it (even a
pure import-only change) would mean its passing result is no longer
self-evidently still true without re-running it live to re-earn evidence
this project already has. The duplication this creates is small and
stable (DB scaffolding, not business logic), which is why it is an
acceptable, disclosed exception rather than a violation of "don't repeat
yourself".

Every price series here is fully deterministic (fixed numpy seeds) and
independently verified by direct run_backtest/test_significance calls
before being wired into any golden_cases.py fixture -- see
docs/explanations/stage-6/step-01-golden-cases.md for the real numbers
each one produced.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.db.models import Verdict as VerdictRow
from agentic_core.schemas import (
    Charter,
    DateRange,
    EffectFamily,
    FalsificationCondition,
    Hypothesis,
    ParsedCharter,
    ParsedHypothesis,
    ParsedStudyDesign,
    StudyDesign,
    UniverseFilter,
)
from agentic_core.study_design import NULL_HYPOTHESIS
from backtester.schema import StrategyRule
from data_pipeline.db.models import PriceBar
from data_pipeline.db.session import SessionFactory


# ---------------------------------------------------------------------------
# Price series -- generalized from verify_stage5_gate.py's build_probe_series
# ---------------------------------------------------------------------------


def build_cyclical_series(
    n_signals: int, dip_pct: float, rally_pct: float, noise_std: float, seed: int, start: str, noise_bars: int = 8
) -> pd.DataFrame:
    """The v3 dip/settle/rally/settle design verify_stage5_gate.py's own
    module docstring documents arriving at after two failed attempts (v1: a
    perfectly periodic staircase, beaten by the control because a fully
    deterministic series gives randomized entries the same shot at every
    jump; v2: dip-then-rally with no settle bar, which went NEGATIVE sharpe
    because backtesting.py fills orders at the NEXT bar's open, not the
    signal bar's close). Generalized here to take its shape parameters as
    arguments rather than module-level constants, since Stage 6 needs six
    differently-parametrized variants, not one.

    Returns a DataFrame with "date"/"close" columns -- the shape
    seed_price_bars consumes, not backtesting.py's own OHLCV format.
    """
    signal_bars = 4
    cycle_len = noise_bars + signal_bars
    rng = np.random.default_rng(seed)
    n_bars = n_signals * cycle_len + 20
    closes = np.empty(n_bars)
    closes[0] = 100.0
    for i in range(1, n_bars):
        pos = i % cycle_len
        prev = closes[i - 1]
        if pos == noise_bars:
            closes[i] = prev * (1 - dip_pct)
        elif pos == noise_bars + 1:
            closes[i] = prev
        elif pos == noise_bars + 2:
            closes[i] = prev * (1 + rally_pct)
        elif pos == noise_bars + 3:
            closes[i] = prev
        else:
            closes[i] = prev * (1 + rng.normal(0, noise_std))
    dates = pd.bdate_range(start, periods=n_bars)
    return pd.DataFrame({"date": [d.date() for d in dates], "close": closes})


def build_random_walk(n_bars: int, noise_std: float, seed: int, start: str) -> pd.DataFrame:
    """A pure geometric random walk -- no engineered dip/rally at all.
    Used for golden_false_no_edge, the "there was never an edge" case,
    deliberately a different generator from build_cyclical_series rather
    than a zero-magnitude call to it, so it reads unambiguously as "no
    signal was planted" rather than "a signal was planted at 0%".
    """
    rng = np.random.default_rng(seed)
    closes = np.empty(n_bars)
    closes[0] = 100.0
    for i in range(1, n_bars):
        closes[i] = closes[i - 1] * (1 + rng.normal(0, noise_std))
    dates = pd.bdate_range(start, periods=n_bars)
    return pd.DataFrame({"date": [d.date() for d in dates], "close": closes})


def seed_price_bars(session, ticker: str, series: pd.DataFrame) -> None:
    """Identical in shape to verify_stage5_gate.py's own helper: raw and
    adjusted OHLC all equal to the single synthetic "close" value, since
    these fixtures have no real corporate-actions history to distinguish
    them (.claude/rules/data-pipeline.md's raw-vs-adjusted split is a
    real-data concern; a synthetic gate fixture has nothing to adjust).
    """
    now = datetime.now(tz=timezone.utc)
    for _, row in series.iterrows():
        price = Decimal(str(round(row["close"], 6)))
        session.add(
            PriceBar(
                ticker=ticker,
                date=row["date"],
                raw_open=price, raw_high=price, raw_low=price, raw_close=price, raw_volume=1_000_000,
                adj_open=price, adj_high=price, adj_low=price, adj_close=price, adj_volume=1_000_000,
                fetched_at=now,
            )
        )
    session.commit()


# ---------------------------------------------------------------------------
# Charter / Hypothesis / StudyDesign rows -- generalized from
# verify_stage5_gate.py's own hand-built-row pattern. Each golden case
# skips Steps 2-3's own LLM parsing (charter confirmation, hypothesis
# generation) the same way Component 8's GATE5PROBE fixture did -- Stage 6
# is chartered around execution+verdict correctness, not
# hypothesis-generation quality, which is a disclosed scope boundary, not
# an oversight (see docs/explanations/stage-6/step-01-golden-cases.md).
# ---------------------------------------------------------------------------


def build_charter_and_hypothesis(
    ticker: str,
    rule: StrategyRule,
    prediction: str,
    falsification_condition: FalsificationCondition,
    rationale: str,
    grounding_tier: str,
    as_of_date: date,
) -> tuple[str, str, Charter, Hypothesis]:
    charter = Charter(
        parsed=ParsedCharter(
            universe=UniverseFilter(sector=None),
            hypothesis_families=[EffectFamily.MEAN_REVERSION],
        ),
        resolved_universe=[ticker],
        screening_as_of=as_of_date,
        screening_group_size=1,
    )
    hypothesis = Hypothesis(
        parsed=ParsedHypothesis(
            rule=rule,
            prediction=prediction,
            falsification_condition=falsification_condition,
            rationale=rationale,
        ),
        grounding_tier=grounding_tier,
        citations=[],
    )

    charter_id, hyp_id = str(uuid.uuid4()), str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(
            CharterRow(
                id=charter_id,
                mandate_text="[Stage 6 golden-set fixture, not a real mandate]",
                charter=charter.model_dump(mode="json"),
                confirmed=True,
                created_at=datetime.now(),
                confirmed_at=datetime.now(),
            )
        )
        session.flush()
        session.add(
            HypothesisRow(
                id=hyp_id,
                charter_id=charter_id,
                rule=hypothesis.parsed.rule.model_dump(mode="json"),
                prediction=hypothesis.parsed.prediction,
                falsification_condition=hypothesis.parsed.falsification_condition.model_dump(mode="json"),
                rationale=hypothesis.parsed.rationale,
                citations=[],
                grounding_tier=grounding_tier,
                status="proposed",
                created_at=datetime.now(),
            )
        )
        session.commit()
    return charter_id, hyp_id, charter, hypothesis


def build_study_design(
    hyp_id: str, in_sample: pd.DataFrame, out_of_sample: pd.DataFrame, split: str = "70/30"
) -> tuple[str, StudyDesign]:
    """Always design_type='simple_holdout' -- study-design QUALITY (would
    this shape have been the right one for this hypothesis) is Component
    5's own already-tested concern, not Stage 6's. Every golden case needs
    exactly one in-sample and one out-of-sample window to exercise the
    execution loop and verdict end to end; nothing here requires
    walk-forward's extra folds.
    """
    design = StudyDesign(
        parsed=ParsedStudyDesign(
            design_type="simple_holdout",
            split=split,
            rationale="Hand-constructed for the Stage 6 golden set, not produced by propose_study_design.",
        ),
        in_sample=DateRange(start=in_sample["date"].iloc[0], end=in_sample["date"].iloc[-1]),
        out_of_sample=DateRange(start=out_of_sample["date"].iloc[0], end=out_of_sample["date"].iloc[-1]),
        null_hypothesis=NULL_HYPOTHESIS,
    )
    design_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(
            StudyDesignRow(
                id=design_id,
                hypothesis_id=hyp_id,
                design=design.model_dump(mode="json"),
                created_at=datetime.now(),
            )
        )
        session.commit()
    return design_id, design


# ---------------------------------------------------------------------------
# Cleanup -- same discipline as verify_stage5_gate.py: delete everything
# this fixture created, then confirm by direct query rather than assuming.
# ---------------------------------------------------------------------------


def cleanup(ticker: str, charter_id: str | None, hyp_id: str | None) -> None:
    with SessionFactory() as session:
        session.execute(delete(PriceBar).where(PriceBar.ticker == ticker))
        if hyp_id:
            run_ids = [
                r[0]
                for r in session.execute(select(StudyRunRow.id).where(StudyRunRow.hypothesis_id == hyp_id)).all()
            ]
            for rid in run_ids:
                session.execute(delete(VerdictRow).where(VerdictRow.study_run_id == rid))
                session.execute(delete(ToolCallTraceRow).where(ToolCallTraceRow.study_run_id == rid))
            session.execute(delete(StudyRunRow).where(StudyRunRow.hypothesis_id == hyp_id))
            session.execute(delete(StudyDesignRow).where(StudyDesignRow.hypothesis_id == hyp_id))
            session.execute(delete(HypothesisRow).where(HypothesisRow.id == hyp_id))
        if charter_id:
            session.execute(delete(CharterRow).where(CharterRow.id == charter_id))
        session.commit()


def verify_cleanup(ticker: str, charter_id: str, hyp_id: str) -> tuple[bool, str]:
    """Returns (ok, detail) rather than verify_stage5_gate.py's own
    record()-and-print -- the harness (src/eval/harness.py) owns reporting
    for the golden set; this module only owns fixture construction and
    teardown.
    """
    with SessionFactory() as session:
        left_bars = session.query(PriceBar).filter(PriceBar.ticker == ticker).count()
        left_hyp = session.get(HypothesisRow, hyp_id)
        left_charter = session.get(CharterRow, charter_id)
    ok = left_bars == 0 and left_hyp is None and left_charter is None
    detail = (
        f"price_bars left={left_bars}, hypothesis left={left_hyp is not None}, "
        f"charter left={left_charter is not None}"
    )
    return ok, detail
