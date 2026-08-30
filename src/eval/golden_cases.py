"""The Stage 6 golden set -- six hand-built, fully deterministic fixtures
with verdicts known IN ADVANCE, per docs/architecture.md Section 9 (planted
true / planted false / known-caveat hypotheses with known correct
verdicts).

Ground truth for every case comes from BUILDING it, never from selecting a
case after the fact because it happened to produce the desired verdict --
exactly the discipline .claude/rules/agent-honesty.md requires of the
agent itself, applied here to the fixtures that test it. Every number
quoted in these docstrings (sharpe, p_value, num_trades) was produced by a
real, direct run_backtest()/test_significance() call on the exact series
and rule below -- not reasoned out on paper -- before this file existed.
See docs/explanations/stage-6/step-01-golden-cases.md for the full
verification record, including two things that did NOT go exactly as
first designed:

1. golden_caveat_thin_sample uses a DIFFERENT grounding_tier than every
   other case here ('whitelist_search', not 'none'). This was not the
   original plan -- it was forced by computing the real corrected
   threshold and finding that 'none' (threshold 0.005) fails this case's
   mandatory_control gate too, not just sample_adequacy, collapsing the
   case's whole point (isolating ONE failing gate). See that function's
   own docstring below.
2. golden_false_breaches_bar realizes fewer trades (26) than a naive
   estimate would predict (~59). The verdict is correct either way by an
   overwhelming margin; the exact mechanism was not traced further, by
   deliberate decision -- see that function's own docstring below.

Each of the six gates a hypothesis's status through exactly ONE of
agentic_core.verdict.decide_status's three independent gates
(pre_registered_falsification, mandatory_control, sample_adequacy) where
that is achievable without entangling a second gate -- so a regression in
any one gate's real-world behavior has a case built specifically to catch
it, not three cases that would all fail together.
"""

from __future__ import annotations

from typing import Callable, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from agentic_core.schemas import Charter, FalsificationCondition, Hypothesis, StudyDesign
from agentic_core.verdict import MIN_TRADES_FOR_CONFIRMATION
from backtester.schema import Comparison, Condition, PriceTerm, ScaledTerm, StrategyRule
from data_pipeline.db.session import SessionFactory
from eval.fixtures import (
    build_charter_and_hypothesis,
    build_cyclical_series,
    build_random_walk,
    build_study_design,
    seed_price_bars,
)

# Same falsification condition for every case, deliberately -- the thing
# under test across all six is whether the SYSTEM reaches the conclusion
# this condition mechanically implies, not whether varied conditions are
# themselves well-chosen (Component 4/agent-honesty.md already own that).
_FALSIFICATION = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.5)


def _leaf(left, op, right) -> Condition:
    return Condition(kind="leaf", comparison=Comparison(left=left, op=op, right=right))


def _dip_rally_rule(name: str, drop_frac: float, rise_frac: float) -> StrategyRule:
    """Buy on a >drop_frac single-bar drop vs the prior close; sell on a
    >rise_frac single-bar rise vs the prior close. The shape
    verify_stage5_gate.py's GATE5PROBE fixture proved -- reused here as a
    named helper because three of the six cases below are built from it or
    its exact mirror image.
    """
    return StrategyRule(
        name=name,
        description=f"Buy on a >{drop_frac:.0%} single-bar drop vs the prior close; "
        f"sell on a >{rise_frac:.0%} single-bar rise vs the prior close.",
        entry=_leaf(
            PriceTerm(field="close"), "lt",
            ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1 - drop_frac),
        ),
        exit=_leaf(
            PriceTerm(field="close"), "gt",
            ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1 + rise_frac),
        ),
    )


class GoldenCase(BaseModel):
    """One golden-set case: a fully seeded, DB-persisted fixture, plus the
    verdict status it must produce. Everything agentic_core.loop_graph
    needs (initial_state, build_graph's design_id/hypothesis_id) and
    everything eval.fixtures.cleanup needs (ticker, charter_id,
    hypothesis_id) is carried on this one object, so the harness never has
    to reconstruct fixture identity from a study_run_id after the fact.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    category: Literal["planted_true", "planted_false", "known_caveat"]
    ticker: str
    charter_id: str
    hypothesis_id: str
    design_id: str
    charter: Charter
    hypothesis: Hypothesis
    design: StudyDesign
    expected_status: Literal["confirmed", "rejected", "inconclusive"]
    expected_caveat_substring: str | None = None


def _seed(ticker: str, in_sample: pd.DataFrame, out_of_sample: pd.DataFrame) -> None:
    with SessionFactory() as session:
        seed_price_bars(session, ticker, pd.concat([in_sample, out_of_sample], ignore_index=True))


# ---------------------------------------------------------------------------
# Planted true -- the agent must CONFIRM. Two independently-parametrized
# variants, directly answering stage-5-summary.md's own "sample size of
# one" criticism of GATE5PROBE without reusing that exact fixture.
# ---------------------------------------------------------------------------


def build_golden_true_1() -> GoldenCase:
    """GATE5PROBE's own v3 shape, re-seeded (verify_stage5_gate.py's fixture
    is not reused directly -- see eval/fixtures.py's module docstring for
    why that script stays untouched).

    VERIFIED (scripts/../scratchpad spike, direct run_backtest/
    test_significance, no LLM): out-of-sample series -> 61 trades,
    sharpe_ratio=0.9318, p_value=0.0033. At grounding_tier='none' and
    hypothesis_count=1 the corrected threshold is 0.005 -- 0.0033 clears
    it, though this is the literal resample floor for n_resamples=300 (not
    one of 300 randomized controls beat the real strategy), so the margin
    is real and fully stable but not large in absolute terms. All three
    decide_status gates pass -> confirmed.
    """
    ticker = "GOLDEN_TRUE_1"
    rule = _dip_rally_rule("golden_true_1", drop_frac=0.07, rise_frac=0.10)
    in_sample = build_cyclical_series(n_signals=60, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=101, start="2020-01-01")
    out_of_sample = build_cyclical_series(n_signals=60, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=102, start="2023-01-01")
    _seed(ticker, in_sample, out_of_sample)

    charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(
        ticker=ticker,
        rule=rule,
        prediction="Golden-set confirm fixture #1: a constructed, statistically decisive edge. Verified "
        "directly against run_backtest/test_significance before being wired into this case "
        "(sharpe=0.9318, p=0.0033 on the out-of-sample series) -- not a literature-grounded "
        "market hypothesis.",
        falsification_condition=_FALSIFICATION,
        rationale="Not grounded in literature -- a Stage 6 golden-set confirm-path fixture. "
        "grounding_tier='none' deliberately: the strictest multiple-comparisons tier, so a "
        "pass here proves the confirm path survives even the harshest correction.",
        grounding_tier="none",
        as_of_date=in_sample["date"].iloc[0],
    )
    design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)
    return GoldenCase(
        name="golden_true_1", category="planted_true", ticker=ticker,
        charter_id=charter_id, hypothesis_id=hyp_id, design_id=design_id,
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status="confirmed",
    )


def build_golden_true_2() -> GoldenCase:
    """A second, independently-parametrized confirm fixture -- different
    dip/rally magnitudes (12%/15% vs golden_true_1's 8%/20%), different
    noise level, different signal count, different seed. Not a re-seed of
    the same design: a genuinely different edge shape, so a pass here is a
    second, non-redundant proof that the confirm path works.

    VERIFIED: out-of-sample series -> 46 trades, sharpe_ratio=1.3061,
    p_value=0.0033 (also the n_resamples=300 floor). At grounding_tier=
    'none', threshold=0.005 -- clears with the same real, stable margin as
    golden_true_1. All three gates pass -> confirmed.
    """
    ticker = "GOLDEN_TRUE_2"
    rule = _dip_rally_rule("golden_true_2", drop_frac=0.10, rise_frac=0.12)
    in_sample = build_cyclical_series(n_signals=45, dip_pct=0.12, rally_pct=0.15, noise_std=0.006, seed=201, start="2020-01-01")
    out_of_sample = build_cyclical_series(n_signals=45, dip_pct=0.12, rally_pct=0.15, noise_std=0.006, seed=202, start="2023-01-01")
    _seed(ticker, in_sample, out_of_sample)

    charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(
        ticker=ticker,
        rule=rule,
        prediction="Golden-set confirm fixture #2: a second, independently-parametrized constructed "
        "edge (verified sharpe=1.3061, p=0.0033), deliberately different in shape from fixture "
        "#1 rather than a re-seed of it.",
        falsification_condition=_FALSIFICATION,
        rationale="Not grounded in literature -- a Stage 6 golden-set confirm-path fixture. "
        "grounding_tier='none' deliberately, matching fixture #1's own harshest-tier "
        "justification.",
        grounding_tier="none",
        as_of_date=in_sample["date"].iloc[0],
    )
    design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)
    return GoldenCase(
        name="golden_true_2", category="planted_true", ticker=ticker,
        charter_id=charter_id, hypothesis_id=hyp_id, design_id=design_id,
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status="confirmed",
    )


# ---------------------------------------------------------------------------
# Planted false -- the agent must REJECT. Three cases, each isolating a
# different one of decide_status's three gates.
# ---------------------------------------------------------------------------


def build_golden_false_no_edge() -> GoldenCase:
    """The realistic "there was never an edge" case: a pure random walk,
    no engineered dip/rally at all. Entry fires on any down day (frequent,
    on ordinary noise) with a fixed 3-bar hold (exit_after_bars, no
    data-dependent exit) -- this specifically routes test_significance to
    the PROBABILITY-based random control (significance.py's own branch on
    rule.exit is None), not the anchored one golden_false_fails_control
    below deliberately exercises instead.

    VERIFIED: out-of-sample series -> 85 trades, sharpe_ratio=-0.8790
    (decisively negative), p_value=0.4518. Fails BOTH
    pre_registered_falsification and mandatory_control, at any sane
    threshold -> rejected. Not built to isolate a single gate -- unlike
    the two cases below, "no edge exists" is not expected to survive one
    gate while failing another.
    """
    ticker = "GOLD_NO_EDGE"
    rule = StrategyRule(
        name="golden_false_no_edge",
        description="Buy on any down day; fixed 3-bar hold. No engineered signal exists in the series "
        "for this rule to have found.",
        entry=_leaf(PriceTerm(field="close"), "lt", PriceTerm(field="close", offset=-1)),
        exit_after_bars=3,
    )
    in_sample = build_random_walk(n_bars=500, noise_std=0.02, seed=300, start="2020-01-01")
    out_of_sample = build_random_walk(n_bars=500, noise_std=0.02, seed=301, start="2023-01-01")
    _seed(ticker, in_sample, out_of_sample)

    charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(
        ticker=ticker,
        rule=rule,
        prediction="Golden-set reject fixture: a pure random walk with no planted signal. Verified "
        "sharpe=-0.8790, p=0.4518 -- fails both the falsification bar and the mandatory "
        "control.",
        falsification_condition=_FALSIFICATION,
        rationale="Not grounded in literature -- a Stage 6 golden-set reject-path fixture, "
        "grounding_tier='none' deliberately.",
        grounding_tier="none",
        as_of_date=in_sample["date"].iloc[0],
    )
    design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)
    return GoldenCase(
        name="golden_false_no_edge", category="planted_false", ticker=ticker,
        charter_id=charter_id, hypothesis_id=hyp_id, design_id=design_id,
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status="rejected",
    )


def build_golden_false_fails_control() -> GoldenCase:
    """The subtle case docs/architecture.md's own mandatory-control
    justification is directly about: "did it beat randomized entries at
    the same trade frequency", not "did it make money".

    Same real rallies as golden_true_1 (identical dip/rally/noise shape),
    same >10% rally exit condition -- but entry fires on ANY ordinary down
    day, not a specifically-timed engineered dip. Because the exit is
    data-dependent, test_significance uses the ANCHORED control
    (make_anchored_random_entry_strategy), which shares the exact same
    exit bars and only randomizes entry timing within the same pre-exit
    gap. If this rule's entries are not actually more informative than a
    random point in that gap, its Sharpe should not beat the anchored
    null -- which is exactly what happened.

    VERIFIED: out-of-sample series -> 62 trades, sharpe_ratio=0.7042
    (clears the naive 0.5 falsification bar on its own), p_value=1.0000
    (the null_mean_sharpe was 0.8003 -- the RANDOM control did better on
    average than this rule). Fails mandatory_control alone, at ANY
    threshold this project's tiers produce -> rejected. This is the one
    case in the whole set where a naive "did it beat the bar" read would
    give the wrong answer, which is exactly why it exists.
    """
    ticker = "GOLD_FAIL_CTRL"
    rule = StrategyRule(
        name="golden_false_fails_control",
        description="Buy on ANY down day (an uninformative entry); sell on the same >10% rally exit "
        "golden_true_1 uses. The exit alone is genuinely profitable -- every entry eventually "
        "rides a real rally -- so this rule's raw Sharpe looks decent. Whether ITS entry "
        "timing adds anything over a random entry in the same pre-exit window is the actual "
        "question, and the anchored control answers it: no.",
        entry=_leaf(PriceTerm(field="close"), "lt", PriceTerm(field="close", offset=-1)),
        exit=_leaf(PriceTerm(field="close"), "gt", ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1.10)),
    )
    in_sample = build_cyclical_series(n_signals=60, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=400, start="2020-01-01")
    out_of_sample = build_cyclical_series(n_signals=60, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=401, start="2023-01-01")
    _seed(ticker, in_sample, out_of_sample)

    charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(
        ticker=ticker,
        rule=rule,
        prediction="Golden-set reject fixture: a rule whose profit comes entirely from a genuinely "
        "profitable exit condition, with an uninformative entry. Verified sharpe=0.7042 "
        "(clears the naive bar) but p=1.0000 against the anchored randomized-entry control "
        "(null_mean_sharpe=0.8003, i.e. random entries did BETTER on average).",
        falsification_condition=_FALSIFICATION,
        rationale="Not grounded in literature -- a Stage 6 golden-set reject-path fixture, "
        "grounding_tier='none' deliberately.",
        grounding_tier="none",
        as_of_date=in_sample["date"].iloc[0],
    )
    design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)
    return GoldenCase(
        name="golden_false_fails_control", category="planted_false", ticker=ticker,
        charter_id=charter_id, hypothesis_id=hyp_id, design_id=design_id,
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status="rejected",
    )


def build_golden_false_breaches_bar() -> GoldenCase:
    """The mirror image of golden_true_1: entry and exit swapped, so the
    rule reliably buys the top of each engineered rally and sells the
    bottom of the following engineered dip -- on the IDENTICAL series
    golden_true_1's out-of-sample window uses (same seed, same
    parameters), deliberately, so the only variable between the two cases
    is which side of each engineered move the rule trades.

    VERIFIED: out-of-sample series -> 26 trades, sharpe_ratio=-4.6928,
    p_value=0.9967. Fails BOTH pre_registered_falsification (by a factor
    of ~9x past the bar in the wrong direction) and mandatory_control, at
    any threshold -> rejected, by an overwhelming margin either way.

    OPEN, DISCLOSED DETAIL, NOT TRACED FURTHER: a naive one-trade-per-
    engineered-cycle estimate on n_signals=60 predicts roughly 59 trades;
    the real number is 26. A plausible cause is entry/exit adjacency
    within the same 4-bar signal block causing some cycles' round trips
    not to close within the sampled window, but this was not worked
    through. This is a deliberate decision, not an oversight: the verdict
    is correct either way by an overwhelming margin, and chasing a
    mechanical curiosity that does not change this case's validity was
    judged not worth the time against the actual harness work remaining.
    If this number ever needs explaining precisely (e.g. because a future
    case relies on trade count matching a tighter estimate), start from
    backtesting.py's own fill-timing rules, the same place Component 8's
    real v1->v2->v3 fixture debugging started.
    """
    ticker = "GOLD_BREACH_BAR"
    rule = StrategyRule(
        name="golden_false_breaches_bar",
        description="Mirror image of golden_true_1: buy on the >10% rally spike (the top), sell on the "
        "following >7% dip (the bottom).",
        entry=_leaf(PriceTerm(field="close"), "gt", ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1.10)),
        exit=_leaf(PriceTerm(field="close"), "lt", ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=0.93)),
    )
    in_sample = build_cyclical_series(n_signals=60, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=100, start="2020-01-01")
    out_of_sample = build_cyclical_series(n_signals=60, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=102, start="2023-01-01")
    _seed(ticker, in_sample, out_of_sample)

    charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(
        ticker=ticker,
        rule=rule,
        prediction="Golden-set reject fixture: the entry/exit-swapped mirror of golden_true_1, on the "
        "identical series. Verified sharpe=-4.6928, p=0.9967 -- fails both the falsification "
        "bar and the mandatory control by a wide margin. Trade count (26) came in lower than "
        "a naive per-cycle estimate (~59); not traced further, disclosed as an open detail "
        "immaterial to the verdict.",
        falsification_condition=_FALSIFICATION,
        rationale="Not grounded in literature -- a Stage 6 golden-set reject-path fixture, "
        "grounding_tier='none' deliberately.",
        grounding_tier="none",
        as_of_date=in_sample["date"].iloc[0],
    )
    design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)
    return GoldenCase(
        name="golden_false_breaches_bar", category="planted_false", ticker=ticker,
        charter_id=charter_id, hypothesis_id=hyp_id, design_id=design_id,
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status="rejected",
    )


# ---------------------------------------------------------------------------
# Known caveat -- the agent must reach INCONCLUSIVE, not confirm or reject.
# ---------------------------------------------------------------------------


def build_golden_caveat_thin_sample() -> GoldenCase:
    """Same clean, real, strong edge as golden_true_1 (identical rule
    shape), but n_signals cut from 60 to 10 so out-of-sample trade count
    lands well under MIN_TRADES_FOR_CONFIRMATION (30). Both other gates
    should pass; only sample_adequacy should fail.

    VERIFIED: out-of-sample series -> 11 trades, sharpe_ratio=0.9519,
    p_value=0.0166.

    DELIBERATE, DISCLOSED DEVIATION FROM THE OTHER FIVE CASES: every other
    case in this module uses grounding_tier='none' (threshold 0.005 at
    hypothesis_count=1). This case does NOT -- it uses
    grounding_tier='whitelist_search' (threshold 0.025) instead. This was
    not the original plan; it was forced by computing the real numbers.
    At tier='none', 0.0166 does NOT clear 0.005 -- this fixture would ALSO
    fail mandatory_control, and decide_status checks falsification and
    control before it ever looks at sample_adequacy
    (`if not gate_falsification.passed or not gate_control.passed: return
    "rejected"`), so a case failing both control and sample_adequacy would
    render as REJECTED, not INCONCLUSIVE -- collapsing this case's entire
    purpose, which is to isolate sample_adequacy as the ONE failing gate.
    grounding_tier='whitelist_search' gives a real, verified margin
    (0.0166 clears 0.025) so this fixture's only failing gate is the one
    it exists to test. A less punishing tier for a thin-sample case is
    also independently defensible on its own terms -- a genuinely
    ambiguous, under-powered result is a different epistemic situation
    from a confidently wrong one, and treating it as such is not a
    stretch -- but the actual reason this tier was chosen was the number,
    not the argument; the argument was found afterward to explain a
    result discovered empirically, not the other way around, and that
    order is stated plainly rather than smoothed over.
    """
    ticker = "GOLD_CAVEAT_THIN"
    rule = _dip_rally_rule("golden_caveat_thin_sample", drop_frac=0.07, rise_frac=0.10)
    in_sample = build_cyclical_series(n_signals=10, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=500, start="2020-01-01")
    out_of_sample = build_cyclical_series(n_signals=10, dip_pct=0.08, rally_pct=0.20, noise_std=0.004, seed=501, start="2023-01-01")
    _seed(ticker, in_sample, out_of_sample)

    charter_id, hyp_id, charter, hypothesis = build_charter_and_hypothesis(
        ticker=ticker,
        rule=rule,
        prediction="Golden-set caveat fixture: the same real edge as golden_true_1, deliberately starved "
        "of signals (n_signals=10 vs 60) so out-of-sample trade count (verified: 11) falls "
        "under the sample-adequacy floor. Verified sharpe=0.9519, p=0.0166.",
        falsification_condition=_FALSIFICATION,
        rationale="Not grounded in literature -- a Stage 6 golden-set caveat-path fixture. "
        "grounding_tier='whitelist_search', NOT 'none' like every other case here -- a "
        "deliberate, disclosed deviation forced by the real corrected threshold; see this "
        "function's own docstring.",
        grounding_tier="whitelist_search",
        as_of_date=in_sample["date"].iloc[0],
    )
    design_id, design = build_study_design(hyp_id, in_sample, out_of_sample)
    return GoldenCase(
        name="golden_caveat_thin_sample", category="known_caveat", ticker=ticker,
        charter_id=charter_id, hypothesis_id=hyp_id, design_id=design_id,
        charter=charter, hypothesis=hypothesis, design=design,
        expected_status="inconclusive",
        expected_caveat_substring=f"{MIN_TRADES_FOR_CONFIRMATION}-trade floor",
    )


GOLDEN_CASE_BUILDERS: list[Callable[[], GoldenCase]] = [
    build_golden_true_1,
    build_golden_true_2,
    build_golden_false_no_edge,
    build_golden_false_fails_control,
    build_golden_false_breaches_bar,
    build_golden_caveat_thin_sample,
]
