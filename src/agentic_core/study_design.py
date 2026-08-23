"""Study design -- turns a confirmed Hypothesis into a pre-registered
StudyDesign: which calendar windows are in-sample vs. out-of-sample, and
(for a persistence/decay claim) how those windows roll forward. See
docs/explanations/stage-5/step-06-study-design.md for the full design
reasoning.

The LLM decides one thing only -- design_type and split, the genuinely fuzzy
call about what shape of experiment this hypothesis needs
(docs/architecture.md Step 3: "this cannot be hardcoded"). Every date in the
result is computed here, from the real cached trading calendar shared by
charter.resolved_universe -- never trusted to the LLM's own arithmetic,
same "model proposes, code disposes" split Component 2 and Component 4
already established.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import func, select

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.hypothesis import hypothesis_from_row
from agentic_core.schemas import Charter, DateRange, Hypothesis, ParsedStudyDesign, StudyDesign
from data_pipeline.db.models import PriceBar
from data_pipeline.db.session import SessionFactory
from llm_client import structured_output

# Relative, never hand-picked -- same reasoning as charter.py's
# CUT_TO_PERCENTILE. ParsedStudyDesign.split is a closed Literal precisely
# so this is the only place a split name becomes a fraction; the LLM never
# emits one.
SPLIT_TO_FRACTION = {"70/30": 0.7, "80/20": 0.8}

# A fold (or a holdout side) shorter than this isn't long enough to hold a
# meaningful number of trades for most rules in this system's registry --
# roughly one trading month. Deliberately a sanity floor on data volume, not
# a hand-picked statistical threshold in the data-pipeline.md sense: the
# actual window sizes above this floor are still fully determined by the
# real trading-day count and the LLM's split/fold choice, not retuned here.
MIN_WINDOW_TRADING_DAYS = 20

# Fixed, not computed per-design: the control Stage 4's test_significance
# runs is unconditional (docs/architecture.md, "the control is MANDATORY"),
# so the null it tests never varies by hypothesis either. See StudyDesign's
# own docstring in schemas.py for why this isn't a stored per-design field.
NULL_HYPOTHESIS = (
    "This rule's trade returns are not distinguishable from randomized "
    "entries at the same trade frequency (Stage 4's test_significance Monte "
    "Carlo permutation test)."
)


class InsufficientHistoryError(Exception):
    """Raised, not retried -- same reasoning as hypothesis.py's
    DuplicateHypothesisError. Covers both a universe with no common trading
    window at all, and an LLM-proposed split/fold count that would make a
    window shorter than MIN_WINDOW_TRADING_DAYS; the caller decides what to
    do next (re-run with a different mandate, a smaller fold count, etc.).
    """


def _common_price_bounds(session, tickers: list[str], history_start: date | None) -> tuple[date, date]:
    """The intersection, not the union, of every ticker's cached date range
    -- so the window this design produces has real data for every ticker in
    it at once. Using the union would let some tickers silently run shorter
    than others, confounding calendar effects with ticker differences in
    exactly the "cross-sectional claim tests the universe together" case
    docs/architecture.md Step 3 describes.
    """
    rows = session.execute(
        select(PriceBar.ticker, func.min(PriceBar.date), func.max(PriceBar.date))
        .where(PriceBar.ticker.in_(tickers))
        .group_by(PriceBar.ticker)
    ).all()
    missing = set(tickers) - {ticker for ticker, _, _ in rows}
    if missing:
        raise InsufficientHistoryError(f"no cached price data for {sorted(missing)}")

    earliest = max(min_date for _, min_date, _ in rows)
    latest = min(max_date for _, _, max_date in rows)
    if history_start is not None:
        earliest = max(earliest, history_start)
    if earliest >= latest:
        raise InsufficientHistoryError(
            f"no common trading window across {tickers} after history_start={history_start}"
        )
    return earliest, latest


def _trading_dates(session, ticker: str, start: date, end: date) -> list[date]:
    """One representative ticker's dates, not a per-ticker union/intersection
    query -- US equities share the same exchange trading calendar (same
    weekends and market holidays), so any one ticker's dates within
    [start, end] are every ticker's dates, and this avoids an unnecessary
    per-ticker query for a universe of any size.
    """
    return list(
        session.execute(
            select(PriceBar.date)
            .where(PriceBar.ticker == ticker, PriceBar.date >= start, PriceBar.date <= end)
            .order_by(PriceBar.date)
        ).scalars()
    )


def _holdout_split_index(trading_dates: list[date], fraction: float) -> int:
    idx = round(len(trading_dates) * fraction)
    return max(1, min(idx, len(trading_dates) - 1))


def _check_window_lengths(*lengths: int) -> None:
    too_short = [n for n in lengths if n < MIN_WINDOW_TRADING_DAYS]
    if too_short:
        raise InsufficientHistoryError(
            f"design would produce a window of {min(too_short)} trading days, "
            f"below the {MIN_WINDOW_TRADING_DAYS}-day minimum"
        )


def _simple_holdout(trading_dates: list[date], fraction: float) -> tuple[DateRange, DateRange]:
    idx = _holdout_split_index(trading_dates, fraction)
    _check_window_lengths(idx, len(trading_dates) - idx)
    return (
        DateRange(start=trading_dates[0], end=trading_dates[idx - 1]),
        DateRange(start=trading_dates[idx], end=trading_dates[-1]),
    )


def _fold_boundaries(trading_dates: list[date], n_folds: int) -> list[DateRange]:
    """Split trading_dates into n_folds consecutive, roughly equal chunks --
    the last fold absorbs any remainder from integer division so every
    trading date in trading_dates ends up in exactly one fold.
    """
    fold_size = len(trading_dates) // n_folds
    _check_window_lengths(*([fold_size] * (n_folds - 1) + [len(trading_dates) - fold_size * (n_folds - 1)]))
    folds = []
    for i in range(n_folds):
        start_idx = i * fold_size
        end_idx = (start_idx + fold_size - 1) if i < n_folds - 1 else len(trading_dates) - 1
        folds.append(DateRange(start=trading_dates[start_idx], end=trading_dates[end_idx]))
    return folds


def _walk_forward_windows(trading_dates: list[date], fraction: float, n_folds: int) -> list[DateRange]:
    """The first `fraction` share of the whole window is one continuous
    in-sample period; the remaining share is chopped into n_folds
    consecutive out-of-sample folds -- so split means the same thing here
    ("in-sample proportion of the total span") as it does for
    simple_holdout, and walk_forward_folds purely controls how finely the
    out-of-sample remainder is divided for the decay check. No
    re-optimization happens between folds: this system's StrategyRules have
    no fit step, so "walk forward" here means re-testing one fixed rule
    across successive unseen periods, not retraining it.
    """
    idx = _holdout_split_index(trading_dates, fraction)
    _check_window_lengths(idx)
    in_sample = DateRange(start=trading_dates[0], end=trading_dates[idx - 1])
    oos_folds = _fold_boundaries(trading_dates[idx:], n_folds)
    return [in_sample, *oos_folds]


def _study_design_prompt(hypothesis: Hypothesis, charter: Charter, n_trading_days: int) -> str:
    fc = hypothesis.parsed.falsification_condition
    return f"""Design the study for this pre-registered hypothesis.

Rule: {hypothesis.parsed.rule.model_dump_json()}
Prediction: {hypothesis.parsed.prediction}
Rationale: {hypothesis.parsed.rationale}
Falsification condition: fails if {fc.metric} is {fc.comparison} {fc.threshold}

Universe: {len(charter.resolved_universe)} tickers
Available common trading history: {n_trading_days} trading days

Two design types are available:
- "simple_holdout": one in-sample window followed by one out-of-sample
  window. Use this by default -- it is the right choice for a plain "does
  this rule beat random entries" claim.
- "walk_forward": the SAME fixed rule (no re-optimization -- this system's
  rules have no fit step) is re-tested across several consecutive,
  chronologically ordered out-of-sample folds, to see whether an edge
  decays over time rather than holding only in one lucky period. Use this
  only when the rationale or prediction is itself about persistence or
  decay over time, not for every hypothesis.

If walk_forward, choose walk_forward_folds so that each fold still has at
least several hundred trading days -- do not propose more folds than
{n_trading_days} available trading days can reasonably support.

split controls what share of the (first, for walk_forward) window is
in-sample versus out-of-sample -- "70/30" or "80/20", never a hand-picked
percentage.
"""


def propose_study_design(hypothesis_id: str) -> tuple[str, StudyDesign]:
    """Raises ValueError if the hypothesis doesn't exist. Raises
    InsufficientHistoryError if the universe has no common trading window,
    or if the LLM's design_type/split/fold choice would produce a window
    shorter than MIN_WINDOW_TRADING_DAYS against the real cached data.
    """
    with SessionFactory() as session:
        hyp_row = session.get(HypothesisRow, hypothesis_id)
        if hyp_row is None:
            raise ValueError(f"no hypothesis with id {hypothesis_id!r}")
        charter_row = session.get(CharterRow, hyp_row.charter_id)
        charter = Charter.model_validate(charter_row.charter)
        hypothesis = hypothesis_from_row(hyp_row)

        earliest, latest = _common_price_bounds(session, charter.resolved_universe, charter.parsed.history_start)
        trading_dates = _trading_dates(session, charter.resolved_universe[0], earliest, latest)

    prompt = _study_design_prompt(hypothesis, charter, len(trading_dates))
    parsed = structured_output(prompt, response_model=ParsedStudyDesign)

    fraction = SPLIT_TO_FRACTION[parsed.split]
    if parsed.design_type == "simple_holdout":
        in_sample, out_of_sample = _simple_holdout(trading_dates, fraction)
        walk_forward_windows = None
    else:
        windows = _walk_forward_windows(trading_dates, fraction, parsed.walk_forward_folds)
        in_sample, out_of_sample = windows[0], windows[1]
        walk_forward_windows = windows

    design = StudyDesign(
        parsed=parsed,
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        walk_forward_windows=walk_forward_windows,
        null_hypothesis=NULL_HYPOTHESIS,
    )

    design_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(
            StudyDesignRow(
                id=design_id,
                hypothesis_id=hypothesis_id,
                design=design.model_dump(mode="json"),
                created_at=datetime.now(),
            )
        )
        session.commit()
    return design_id, design
