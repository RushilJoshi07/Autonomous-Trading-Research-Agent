"""The verdict -- Stage 5, Component 7. This is where Sacred Gate 2 is
satisfied. See docs/explanations/stage-5/step-09-verdict.md for the full
design reasoning.

Two claims have to hold here, and different machinery enforces each:

1. THE AGENT NEVER FABRICATES. Every quantitative claim carries a reference
   to the tool call that produced it, and validate_claims checks the number
   against the real tool_call_traces row. A claim with no valid reference is
   rejected, and so is a number appearing in the narrative without a
   matching claim behind it (scan_for_unreferenced_numbers).

2. THE AGENT KILLS ITS OWN HYPOTHESES. decide_status is deterministic code
   reading real numbers, and the LLM is TOLD the status before it writes a
   word. There is no field on ParsedVerdict it could set to change the
   outcome.

The subtlety that makes (2) harder than it looks: "mechanical" does not
automatically mean "honest", because a human still chooses WHICH DATA the
mechanical rule reads. Scoring this project's real walk-forward study on
its final window alone gives sharpe=0.941 -> not falsified -> confirmed;
scoring every out-of-sample window gives window 1 at -1.510 -> falsified ->
rejected. Same pre-registered condition, same data, opposite verdict. The
scope decision below is therefore load-bearing, and
tests/agentic_core/test_verdict.py mutates it deliberately to prove the
suite catches a narrowing.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.db.models import Verdict as VerdictRow
from agentic_core.hypothesis import hypothesis_from_row
from agentic_core.loop_state import flatten_windows
from agentic_core.schemas import (
    Charter,
    Claim,
    FalsificationCondition,
    Hypothesis,
    ParsedVerdict,
    StudyDesign,
    Verdict,
)
from data_pipeline.db.session import SessionFactory
from llm_client import StructuredOutputError, structured_output

BASE_ALPHA = 0.05

MAX_VERDICT_ATTEMPTS = 3

# Tolerance for matching a claimed number against the real traced value.
# Generous enough that a model rounding 0.6519 to 0.65 still validates,
# tight enough that a genuinely different number does not.
_REL_TOLERANCE = 0.01
_ABS_TOLERANCE = 0.01

# PROVISIONAL -- approved as a deliberately-labelled placeholder, not a
# calibrated figure. This is the ONE number in this module with no
# literature anchor behind it: 30 is the conventional small-sample rule of
# thumb and nothing more.
#
# It is safe to ship at this confidence ONLY because of the asymmetry
# enforced in decide_status: too few trades can downgrade a would-be
# CONFIRMATION to "inconclusive", but can never rescue a failure. The
# direction of error is therefore always toward caution. If this number is
# wrong, the cost is an over-cautious "insufficient evidence" on a real
# edge -- never a confirmed hypothesis that should have been killed.
#
# REVISIT TRIGGER: once any charter has produced several confirmations,
# check whether windows in the 20-40 trade range behaved differently from
# windows well above it. Until then there is nothing to calibrate against.
MIN_TRADES_FOR_CONFIRMATION = 30

# PROVISIONAL -- the grounding prior, expressed as an ASSUMED EFFECTIVE
# SEARCH BURDEN rather than as an alpha multiplier.
#
# The mechanism matters as much as the numbers. An alpha multiplier is a
# fudge factor; "effective tests" states a claim that can be argued with --
# how many alternatives do we assume were implicitly searched to produce
# this hypothesis? docs/architecture.md's own wording ("an ungrounded
# hypothesis is closer to random search, so it faces a STRICTER bar") is
# exactly a statement about search burden, so that is what this encodes.
#
#   local_corpus     1.0  -- came from a curated paper. The search that
#                            found it happened outside this system, is
#                            documented by citation, and survived peer
#                            review. No additional burden assumed.
#   whitelist_search 2.0  -- Tier 2 found *a* paper via live search over
#                            academic domains, but nothing curated or
#                            verified it. Deliberately mild: the evidence
#                            is real, just less vetted.
#   none            10.0  -- an order-of-magnitude marker meaning "assume
#                            roughly ten alternatives were implicitly
#                            weighed". Not a measurement.
#
# WHAT IS NOT CLAIMED: 10.0 puts the ungrounded threshold near Harvey, Liu
# & Zhu (2016)'s recommended strictness (t>3.0 ~ p<0.0027). That is a
# SANITY CHECK THAT THE NUMBER IS NOT ABSURD -- explicitly NOT a
# derivation. HLZ's threshold is deliberately not adopted directly: the
# corpus carries Chen & Zimmermann (2022) specifically as a counterpoint
# ("giving balanced methodological grounding rather than a one-sided view",
# per data/corpus/paper_list.json), and picking one side of a disagreement
# the corpus was built to represent honestly would be the wrong move. Both
# papers are ingested and retrievable.
#
# REVISIT TRIGGER: once >=20 hypotheses have been tested under any charter,
# compare confirmation rates by grounding tier. Similar rates across tiers
# means the penalty is too high; a far higher ungrounded confirmation rate
# means it is too low.
TIER_SEARCH_BURDEN: dict[str, float] = {
    "local_corpus": 1.0,
    "whitelist_search": 2.0,
    "none": 10.0,
}

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

# A hypothesis's own auto-generated name often embeds a digit as a unit
# abbreviation -- "52W_High_Proximity_Momentum" -- and the model naturally
# echoes that name in prose ("the 52-week-high proximity strategy"). That
# "52" is a self-reference to a name already validated at hypothesis-
# generation time, not a fresh evidentiary claim, and scan_for_unreferenced_
# numbers must not flag it -- but must still flag a genuinely fabricated
# number that happens to share the same digit ("a 52% win rate").
_NAME_DIGIT_LETTER_RE = re.compile(r"(\d+(?:\.\d+)?)([A-Za-z])")
_SELF_REFERENCE_GAP_RE = re.compile(r"^[\s-]{0,2}")

# Matching on the first letter alone is not enough to confirm the SAME word:
# "52 winning trades" or "52 weighted signals" also start with "w" and would
# wrongly pass. Requiring membership in this small, closed, stable category
# (not a letter->meaning dictionary -- it maps nothing, it only tests set
# membership) is what actually distinguishes a real unit word from any other
# word that happens to share a first letter. Extend this set if the
# codebase's own hypothesis names start using a period word not listed here.
#
# "session"/"sessions" was checked against, not just assumed absent from,
# this project's own naming: grepped src/backtester/schema.py and
# src/backtester/extended_indicators.py (every place an indicator or period
# word is actually defined) and it appears nowhere as a time unit. Left out
# rather than added speculatively -- add it only once a real hypothesis name
# actually uses it, the same "extend on real evidence, not preemptively"
# discipline TIER_SEARCH_BURDEN and MIN_TRADES_FOR_CONFIRMATION already use
# elsewhere in this module.
_TIME_UNIT_WORDS = {"day", "days", "week", "weeks", "month", "months", "quarter", "quarters", "year", "years"}
_WORD_RE = re.compile(r"[a-z]+")


def _self_reference_digit_letters(hypothesis_name: str) -> dict[float, str]:
    """Maps each digit-run in the name that's immediately followed by a
    letter to that letter, lowercased -- e.g. {52.0: 'w'} for "52W_High...".

    Deliberately does NOT try to expand the letter into a word ("w" ->
    "week"): a hardcoded abbreviation table would need updating for every
    unit letter a future hypothesis name happens to use, and would still
    miss one nobody anticipated. Matching on the letter alone generalizes
    to any of them for free -- see _is_self_reference for how.
    """
    return {float(m.group(1)): m.group(2).lower() for m in _NAME_DIGIT_LETTER_RE.finditer(hypothesis_name)}


def _is_self_reference(narrative: str, position: int, letter: str) -> bool:
    """True if the text right after a flagged number -- skipping up to one
    hyphen or space ("52-week", "52 week") -- is a genuine time-unit word
    (see _TIME_UNIT_WORDS) starting with the SAME letter that follows the
    number in the hypothesis's own name ("52W" -> "w"). Covers "52w"/
    "52-week"/"52 week"/"52 weeks" with one rule instead of enumerating each
    surface form. Requires BOTH conditions, not just the letter: "52%"/
    "52 million" fail because their next letter doesn't match "w", and
    "52 winning trades"/"52 weighted signals" fail despite starting with the
    right letter because "winning"/"weighted" are not time-unit words --
    a fabricated claim does not get a pass just because it happens to start
    with the same letter as the hypothesis's own abbreviation.

    This is a WHOLE-WORD match, not a prefix match, and that is load-bearing
    rather than incidental: `_WORD_RE.match(rest)` is greedy, so it captures
    the entire contiguous run of letters right after the number ("weekend",
    "weeklong" -- not "week") BEFORE the set-membership check ever runs.
    `"weekend" in _TIME_UNIT_WORDS` and `"weeklong" in _TIME_UNIT_WORDS` are
    both False under Python's exact set equality, so "52 weekend momentum"
    and "52weeklong rally" are correctly rejected as self-references despite
    starting with "week" -- see test_a_number_followed_by_a_longer_word_
    starting_with_a_unit_word_is_not_a_self_reference, which locks this in.
    """
    tail = narrative[position : position + 16].lower()
    gap = _SELF_REFERENCE_GAP_RE.match(tail)
    rest = tail[gap.end() :]
    word_match = _WORD_RE.match(rest)
    word = word_match.group(0) if word_match else ""
    return word in _TIME_UNIT_WORDS and word.startswith(letter)


class VerdictValidationError(Exception):
    """Raised when the LLM's verdict could not be validated after retries.

    No Verdict row is written when this fires. A verdict whose claims do not
    resolve is not a weaker verdict -- it is an unsupported one, and storing
    it would defeat the only mechanism that makes fabrication checkable.

    Carries `errors` (the last attempt's validation failures) and
    `narrative` (what the model actually wrote), for the same reason
    StructuredOutputError carries raw_response: a rejection that does not
    say WHAT failed forces whoever hits it to re-derive it by hand, and
    the distinction between "the model fabricated" and "the allowlist is
    too tight" is exactly the one that needs to be visible.
    """

    def __init__(self, message: str, *, errors: list[str], narrative: str | None = None) -> None:
        super().__init__(message)
        self.errors = errors
        self.narrative = narrative


class WindowEvaluation(BaseModel):
    """One window's evidence, assembled from real traces.

    backtest_trace_id / significance_trace_id are carried through so the
    prompt can hand the model the exact reference each number needs, which
    is what makes a validated claim possible at all -- a model that has to
    guess a trace id will guess wrong and the claim will be rejected.
    """

    window_index: int
    is_out_of_sample: bool
    metric_value: float | None
    num_trades: int
    p_value: float | None
    backtest_trace_id: int | None
    significance_trace_id: int | None


class GateResult(BaseModel):
    name: str
    passed: bool
    detail: str
    windows_failing: list[int]


def corrected_threshold(hypothesis_count: int, grounding_tier: str) -> float:
    """Bonferroni-style on the count SO FAR, deliberately -- not the
    Benjamini-Hochberg that research_stats.multiple_comparisons already
    implements.

    BH corrects a LIST of p-values simultaneously; it needs the full set.
    Verdicts render sequentially, one per completed study, and a hypothesis
    that has not been tested yet has no p-value to include -- so BH is not
    applicable at this point in time, however well it fits the problem in
    principle.

    What happens instead: a sequential-safe, conservative threshold now,
    with the raw p-values and the count preserved in the verdict row so a
    proper cross-charter BH re-evaluation is possible later without
    re-running anything. That re-evaluation can DEMOTE an earlier
    confirmation as evidence accumulates, which is exactly the "previously
    believed, now decayed" concept docs/architecture.md Step 6 already
    describes -- so it belongs to the scoreboard, not here.
    """
    burden = TIER_SEARCH_BURDEN[grounding_tier]
    effective_tests = max(1, hypothesis_count) * burden
    return BASE_ALPHA / effective_tests


def _metric_from(sources: list[dict | None], metric: str) -> float | None:
    """A falsification metric can live in either trace: sharpe_ratio and
    num_trades come from run_backtest, p_value from test_significance. The
    FalsificationCondition vocabulary spans both (see schemas.py), so
    looking in only one would silently return None for a perfectly valid
    p_value condition and make the gate unevaluable.
    """
    for src in sources:
        if src and metric in src and src[metric] is not None:
            return float(src[metric])
    return None


def evaluate_windows(
    traces: list[ToolCallTraceRow], n_windows: int, metric: str
) -> list[WindowEvaluation]:
    backtests = {t.window_index: t for t in traces if t.tool_name == "run_backtest" and not t.is_error}
    sigs = {t.window_index: t for t in traces if t.tool_name == "test_significance" and not t.is_error}

    evaluations = []
    for w in range(n_windows):
        bt, sig = backtests.get(w), sigs.get(w)
        evaluations.append(
            WindowEvaluation(
                window_index=w,
                # Window 0 is the in-sample window in every design
                # (flatten_windows puts it first for both design types).
                is_out_of_sample=w > 0,
                metric_value=_metric_from([bt.result if bt else None, sig.result if sig else None], metric),
                num_trades=int(bt.result.get("num_trades", 0)) if bt else 0,
                p_value=float(sig.result["p_value"]) if sig else None,
                backtest_trace_id=bt.id if bt else None,
                significance_trace_id=sig.id if sig else None,
            )
        )
    return evaluations


def _fails_bar(value: float, condition: FalsificationCondition) -> bool:
    if condition.comparison == "less_than":
        return value < condition.threshold
    return value > condition.threshold


def decide_status(
    evaluations: list[WindowEvaluation],
    condition: FalsificationCondition,
    threshold: float,
) -> tuple[str, list[GateResult]]:
    """The verdict, decided by code reading real numbers. No LLM anywhere in
    this function or anything it calls.

    SCOPE IS THE LOAD-BEARING DECISION. Both gates read EVERY out-of-sample
    window, not the final one and not an aggregate:

      - Final-window-only would score a walk-forward study on its ending,
        discarding the entire reason the design exists. On this project's
        real study it flips the verdict from rejected to confirmed.
      - An aggregate (mean across folds) hides instability, which is
        precisely what a -1.510 fold sitting between two positive ones IS.

    In-sample (window 0) is reported but never decisive. It is the period
    the hypothesis was formed against -- the weakest evidence in the study
    -- so letting it drive the outcome in either direction over-weights it.
    """
    oos = [e for e in evaluations if e.is_out_of_sample]

    unevaluable = [e.window_index for e in oos if e.metric_value is None]
    if not oos or unevaluable:
        return "inconclusive", [
            GateResult(
                name="evidence_present",
                passed=False,
                detail=(
                    "no out-of-sample evidence to evaluate"
                    if not oos
                    else f"windows {unevaluable} have no value for {condition.metric!r}"
                ),
                windows_failing=unevaluable,
            )
        ]

    falsified = [e.window_index for e in oos if _fails_bar(e.metric_value, condition)]
    gate_falsification = GateResult(
        name="pre_registered_falsification",
        passed=not falsified,
        detail=(
            f"fails if {condition.metric} {condition.comparison} {condition.threshold}; "
            f"out-of-sample windows failing: {falsified or 'none'}"
        ),
        windows_failing=falsified,
    )

    no_control = [e.window_index for e in oos if e.p_value is None or e.p_value >= threshold]
    gate_control = GateResult(
        name="mandatory_control",
        passed=not no_control,
        detail=(
            f"every out-of-sample window must beat randomized entries at p < {threshold:.4f}; "
            f"windows failing: {no_control or 'none'}"
        ),
        windows_failing=no_control,
    )

    thin = [e.window_index for e in oos if e.num_trades < MIN_TRADES_FOR_CONFIRMATION]
    gate_sample = GateResult(
        name="sample_adequacy",
        passed=not thin,
        detail=(
            f"a confirmation needs >= {MIN_TRADES_FOR_CONFIRMATION} trades per out-of-sample "
            f"window; windows below: {thin or 'none'}"
        ),
        windows_failing=thin,
    )

    gates = [gate_falsification, gate_control, gate_sample]

    # ASYMMETRY, deliberately: failure gates are checked FIRST, so thin
    # evidence can downgrade a would-be confirmation to "inconclusive" but
    # can never rescue a hypothesis the evidence already killed. You can
    # always reject on evidence; you can only confirm with enough of it.
    if not gate_falsification.passed or not gate_control.passed:
        return "rejected", gates
    if not gate_sample.passed:
        return "inconclusive", gates
    return "confirmed", gates


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_ABS_TOLERANCE, _REL_TOLERANCE * abs(b))


def validate_claims(claims: list[Claim], traces: list[ToolCallTraceRow]) -> list[str]:
    """Returns a list of human-readable errors; empty means every claim
    resolved. This is Gate 2's fabrication half, implemented in code rather
    than hoped for in a prompt.

    by_id is built ONLY from this study run's traces, which is what makes
    the cross-study check work: a claim citing a real trace id belonging to
    a different run resolves to nothing here and is rejected. Without that
    scoping, a model could support a claim with another study's evidence
    and the reference would look perfectly valid.
    """
    by_id = {t.id: t for t in traces}
    errors = []
    for i, claim in enumerate(claims):
        trace = by_id.get(claim.tool_call_trace_id)
        if trace is None:
            errors.append(
                f"claim {i} references tool_call_trace_id={claim.tool_call_trace_id}, "
                "which does not exist in this study run"
            )
            continue
        if trace.is_error:
            errors.append(f"claim {i} references trace {trace.id}, which recorded an error")
            continue
        actual = trace.result.get(claim.metric)
        if actual is None:
            errors.append(
                f"claim {i} cites metric {claim.metric!r}, absent from trace {trace.id} "
                f"({trace.tool_name})"
            )
            continue
        if not _close(claim.value, float(actual)):
            errors.append(
                f"claim {i} states {claim.metric}={claim.value}, but trace {trace.id} "
                f"recorded {actual}"
            )
    return errors


def scan_for_unreferenced_numbers(
    narrative: str, claims: list[Claim], allowed: set[float], hypothesis_name: str = ""
) -> list[str]:
    """Closes the hole validate_claims alone leaves open.

    A model could keep the claims list scrupulously clean and still write a
    fabricated number into the prose -- the claims would all validate, and
    the invented figure would ride along unchecked. So every numeric token
    in the narrative must resolve to either a validated claim's value, a
    structural value from the caller's allowlist (window count, hypothesis
    count, the threshold, the design's own years), or a self-reference to a
    digit embedded in the hypothesis's own already-validated name (see
    _is_self_reference) -- checked per OCCURRENCE, not per digit string, so
    a legitimate self-reference in one place never clears a genuinely
    fabricated number sharing the same digit elsewhere in the same
    narrative. hypothesis_name defaults to "" (no self-references possible)
    so every existing call site not touched by this exception keeps working
    unchanged.

    Deliberately operates on the LLM's narrative ONLY, before mandatory
    caveats are appended -- those are code-generated and contain code-chosen
    numbers, so scanning them would be checking this module's own output
    against itself.
    """
    claimed = [c.value for c in claims]
    self_ref_letters = _self_reference_digit_letters(hypothesis_name)
    orphans = []
    for match in _NUMBER_RE.finditer(narrative):
        token = match.group(0)
        value = float(token)
        if any(_close(value, c) for c in claimed):
            continue
        if any(_close(value, a) for a in allowed):
            continue
        letter = self_ref_letters.get(value)
        if letter is not None and _is_self_reference(narrative, match.end(), letter):
            continue
        orphans.append(token)
    return [f"narrative contains {t!r}, which matches no validated claim" for t in sorted(set(orphans))]


def mandatory_caveats(
    hypothesis_count: int,
    grounding_tier: str,
    threshold: float,
    evaluations: list[WindowEvaluation],
    charter: Charter,
) -> list[str]:
    """Code-generated, never requested from the model.

    docs/architecture.md Step 9 scores "whether required caveats appeared",
    which only means something if they cannot be omitted. An agreeable model
    asked politely for caveats will sometimes skip the inconvenient one;
    generating them here removes the opportunity.
    """
    caveats = [
        f"This is hypothesis {hypothesis_count} tested under this charter. The significance "
        f"threshold was adjusted accordingly to p < {threshold:.4f}, assuming an effective "
        f"search burden of {TIER_SEARCH_BURDEN[grounding_tier]}x for grounding tier "
        f"'{grounding_tier}'. That burden factor is a provisional assumption, not a "
        f"calibrated measurement.",
        "Universe membership is current, not point-in-time: delisted and bankrupt names are "
        "absent, so results are biased upward by an amount this study does not measure.",
    ]
    thin = [e.window_index for e in evaluations if e.is_out_of_sample and e.num_trades < MIN_TRADES_FOR_CONFIRMATION]
    if thin:
        detail = ", ".join(
            f"window {e.window_index}: {e.num_trades} trades"
            for e in evaluations
            if e.window_index in thin
        )
        caveats.append(
            f"Out-of-sample windows below the {MIN_TRADES_FOR_CONFIRMATION}-trade floor "
            f"({detail}) carry too little evidence to support a confirmation on their own."
        )
    return caveats


def _verdict_prompt(
    hypothesis: Hypothesis,
    design: StudyDesign,
    evaluations: list[WindowEvaluation],
    status: str,
    gates: list[GateResult],
    threshold: float,
) -> str:
    """The model is told the OUTCOME first and asked to explain it.

    Ordering is deliberate: a prompt that presented the evidence and then
    asked "what do you conclude?" would be inviting exactly the judgement
    call .claude/rules/agent-honesty.md says must never be the LLM's --
    and an agreeable model looking at a +0.941 final fold has a friendly
    story readily available. Stating the decision up front makes the task
    unambiguously expository.
    """
    fc = hypothesis.parsed.falsification_condition
    rows = []
    for e in evaluations:
        label = "IN-SAMPLE" if not e.is_out_of_sample else "out-of-sample"
        rows.append(
            f"  window {e.window_index} ({label}): {fc.metric}={e.metric_value}, "
            f"num_trades={e.num_trades}, p_value={e.p_value}  "
            f"[backtest trace id={e.backtest_trace_id}, significance trace id={e.significance_trace_id}]"
        )
    gate_lines = [f"  - {g.name}: {'PASS' if g.passed else 'FAIL'} -- {g.detail}" for g in gates]

    return f"""Write the verdict for a completed study. The outcome has ALREADY BEEN DECIDED
by deterministic code reading the recorded tool outputs. Your job is to explain it,
not to reach it.

VERDICT: {status.upper()}

Hypothesis: {hypothesis.parsed.prediction}
Rule: {hypothesis.parsed.rule.name}
Pre-registered before any testing: FAILS if {fc.metric} {fc.comparison} {fc.threshold}
Design: {design.parsed.design_type}, {len(evaluations)} windows
Corrected significance threshold: p < {threshold:.4f}

Evidence (every number below came from a recorded tool call):
{chr(10).join(rows)}

Gates evaluated by code:
{chr(10).join(gate_lines)}

Write:
- narrative: a plain, honest explanation of why the verdict is {status}. Do not soften
  a rejection or hedge toward optimism. If the hypothesis is dead, say so directly.
- claims: EVERY number you use in the narrative must appear here as a claim with the
  tool_call_trace_id it came from, the metric name, and the value. A number in your
  narrative that has no matching claim will cause this verdict to be REJECTED and
  rewritten. Do not cite a number you were not given above.
- caveats: any additional limitations worth stating. Required disclosures about
  hypothesis count, universe bias, and sample size are added by code -- do not
  duplicate them.
"""


def _structural_allowlist(
    evaluations: list[WindowEvaluation],
    design: StudyDesign,
    hypothesis_count: int,
    threshold: float,
    condition: FalsificationCondition,
) -> set[float]:
    """Numbers the narrative may legitimately contain that are not claims:
    window indices and counts, the hypothesis count, the corrected
    threshold, the pre-registered bar, the trade floor, and the design's own
    calendar years.

    Kept deliberately tight -- each entry is a specific known constant, not
    a category. A broad allowlist ("any integer under 100") would let a
    fabricated trade count or Sharpe pass as structural, which is exactly
    the hole this scan exists to close.

    condition.threshold and MIN_TRADES_FOR_CONFIRMATION were both missing
    from the first version, and the first live run caught it: the model
    correctly wrote "requires all out-of-sample Sharpe ratios to be at least
    0.5" and "far fewer than the required 30 trades", and both true,
    system-supplied numbers were flagged as unreferenced. That was a
    too-tight allowlist rejecting honest prose, not a model fabricating --
    a distinction only visible because VerdictValidationError carries the
    errors and the narrative.
    """
    allowed = {
        float(len(evaluations)),
        float(hypothesis_count),
        threshold,
        float(condition.threshold),
        float(MIN_TRADES_FOR_CONFIRMATION),
    }
    allowed |= {float(e.window_index) for e in evaluations}
    for w in flatten_windows(design):
        allowed |= {float(w.start.year), float(w.end.year)}
    return allowed


def render_verdict(study_run_id: str, llm=structured_output) -> tuple[str, Verdict]:
    """Reads the traces the loop wrote, decides the status in code, has the
    model write prose around numbers already locked to those traces,
    validates every claim, and writes one Verdict row.

    llm is injected for the same reason build_graph injects it: the whole
    validation path is then testable with no Bedrock spend.
    """
    with SessionFactory() as session:
        run = session.get(StudyRunRow, study_run_id)
        if run is None:
            raise ValueError(f"no study_run with id {study_run_id!r}")
        if run.status != "completed":
            raise ValueError(
                f"study_run {study_run_id!r} has status {run.status!r}; a verdict is only "
                "written for a completed run -- a failed run has untested windows, and a "
                "verdict drawn from partial evidence is exactly what this component exists "
                "to prevent"
            )
        hyp_row = session.get(HypothesisRow, run.hypothesis_id)
        hypothesis = hypothesis_from_row(hyp_row)
        charter = Charter.model_validate(session.get(CharterRow, hyp_row.charter_id).charter)
        design = StudyDesign.model_validate(session.get(StudyDesignRow, run.study_design_id).design)
        traces = list(
            session.execute(
                select(ToolCallTraceRow)
                .where(ToolCallTraceRow.study_run_id == study_run_id)
                .order_by(ToolCallTraceRow.step_index)
            ).scalars()
        )
        hypothesis_count = len(
            session.execute(
                select(HypothesisRow.id).where(HypothesisRow.charter_id == hyp_row.charter_id)
            ).all()
        )

    fc = hypothesis.parsed.falsification_condition
    windows = flatten_windows(design)
    evaluations = evaluate_windows(traces, len(windows), fc.metric)
    threshold = corrected_threshold(hypothesis_count, hypothesis.grounding_tier)
    status, gates = decide_status(evaluations, fc, threshold)

    allowed = _structural_allowlist(evaluations, design, hypothesis_count, threshold, fc)
    base = _verdict_prompt(hypothesis, design, evaluations, status, gates, threshold)
    prompt = base

    parsed: ParsedVerdict | None = None
    errors: list[str] = []
    last_narrative: str | None = None
    for attempt in range(1, MAX_VERDICT_ATTEMPTS + 1):
        try:
            candidate = llm(prompt, response_model=ParsedVerdict)
        except StructuredOutputError as e:
            errors = [f"structured output rejected: {str(e)[:300]}"]
            prompt = f"{base}\n\n--- YOUR PREVIOUS RESPONSE WAS REJECTED (attempt {attempt}) ---\n{str(e)[:500]}\n"
            continue

        last_narrative = candidate.narrative
        errors = validate_claims(candidate.claims, traces)
        errors += scan_for_unreferenced_numbers(
            candidate.narrative, candidate.claims, allowed, hypothesis.parsed.rule.name
        )
        if not errors:
            parsed = candidate
            break

        prompt = (
            f"{base}\n\n--- YOUR PREVIOUS VERDICT WAS REJECTED (attempt {attempt}) ---\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n\nEvery number in the narrative must have a matching claim citing the "
            "trace id it came from. Rewrite the verdict.\n"
        )

    if parsed is None:
        raise VerdictValidationError(
            f"verdict for study_run {study_run_id!r} failed validation after "
            f"{MAX_VERDICT_ATTEMPTS} attempts; no verdict row written",
            errors=errors,
            narrative=last_narrative,
        )

    caveats = mandatory_caveats(hypothesis_count, hypothesis.grounding_tier, threshold, evaluations, charter)
    caveats += [c for c in parsed.caveats if c not in caveats]

    verdict = Verdict(
        parsed=parsed,
        status=status,
        hypothesis_count_under_charter=hypothesis_count,
        corrected_significance_threshold=threshold,
        caveats=caveats,
    )

    verdict_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(
            VerdictRow(
                id=verdict_id,
                study_run_id=study_run_id,
                status=status,
                claims=[c.model_dump(mode="json") for c in parsed.claims],
                hypothesis_count_under_charter=hypothesis_count,
                corrected_significance_threshold=threshold,
                narrative=parsed.narrative,
                caveats=caveats,
                created_at=datetime.now(),
            )
        )
        # The first component permitted to set a final status. Component 6
        # deliberately left the hypothesis on 'testing'.
        hyp = session.get(HypothesisRow, run.hypothesis_id)
        hyp.status = status
        session.commit()

    return verdict_id, verdict
