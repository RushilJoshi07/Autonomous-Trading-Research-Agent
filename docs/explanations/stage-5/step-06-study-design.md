# Stage 5, Component 5: study design

## 1. What this component does

This component turns a confirmed `Hypothesis` (Component 4's output — a
`StrategyRule`, a prediction, a pre-registered falsification condition, a
rationale) into a `StudyDesign`: a concrete, immutable record of which
calendar dates are in-sample versus out-of-sample, and — for a hypothesis
that specifically claims an edge persists or decays over time — how those
dates roll forward into several successive test windows.

Nothing in this component runs a backtest. It only plans one. The
`study_designs` table it writes to has existed since Component 1's schema
migration, sitting empty; this is the first code that fills it. The design
this component produces is what Component 6 (the execution loop, not yet
built) will later read to decide which `start`/`end` dates to actually pass
into Stage 4's `run_backtest` and `test_significance` MCP tools.

**Scope boundary — what this component does NOT do:** it does not enforce
that out-of-sample data stays hidden while the in-sample phase runs. That
enforcement (`docs/architecture.md` Step 3: "code enforces the data split,
not the prompt") is Component 6's job — the execution loop simply never
calls a tool with out-of-sample dates until the in-sample phase concludes.
This component's job ends at producing a *correct* plan; making that plan
*impossible to violate* is downstream work.

New code: `src/agentic_core/study_design.py` (the whole module),
`src/agentic_core/schemas.py` (`DateRange`, `ParsedStudyDesign`,
`StudyDesign`), `src/agentic_core/hypothesis.py` (one new function,
`hypothesis_from_row`), `tests/agentic_core/test_study_design.py` (14 new
tests).

---

## 2. Every meaningful line explained

### `schemas.py` — `DateRange`

```python
class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> "DateRange":
        if self.start > self.end:
            raise ValueError(f"start ({self.start}) is after end ({self.end})")
        return self
```

One inclusive `[start, end]` window, reused for both the in-sample/
out-of-sample pair and every walk-forward fold. The alternative was two
separate types — one for a holdout window, one for a fold — since they're
conceptually used differently. Rejected: they are the *same shape* (a
start date and an end date with the same ordering constraint), and giving
them different types would mean writing the same order-validator twice,
and would force `walk_forward_windows` to be a different element type than
`in_sample`/`out_of_sample`, which breaks the "read `in_sample` the same
way regardless of design type" guarantee described below. The validator
itself exists because nothing later in the pipeline should ever have to
re-check that a date range isn't inverted — if this validator didn't
exist, an inverted range could reach `run_backtest` in Component 6 and
either crash confusingly deep in `backtesting.py` or, worse, silently
return an empty/degenerate backtest that looks like a real (if boring)
result.

### `schemas.py` — `ParsedStudyDesign`

```python
class ParsedStudyDesign(BaseModel):
    design_type: Literal["simple_holdout", "walk_forward"]
    split: Literal["70/30", "80/20"]
    walk_forward_folds: int | None = Field(default=None, ge=2)
    rationale: str

    @model_validator(mode="after")
    def _check_folds_match_design_type(self) -> "ParsedStudyDesign":
        if self.design_type == "walk_forward" and self.walk_forward_folds is None:
            raise ValueError("walk_forward requires walk_forward_folds")
        if self.design_type == "simple_holdout" and self.walk_forward_folds is not None:
            raise ValueError("simple_holdout must not specify walk_forward_folds")
        return self
```

This is exactly what `llm_client.structured_output` is asked to produce —
the one and only place in this component where the LLM's judgment is
trusted. `design_type` is the genuinely fuzzy call: does this hypothesis's
*rationale and prediction* read as a plain "does this rule beat random
entries" claim, or as a persistence/decay claim that specifically needs
re-testing across time? That can't be decided from the `StrategyRule`
alone (the rule is just entry/exit logic; nothing in it says whether the
hypothesis is *about* time-stability) — it has to come from reading the
natural-language rationale, which is exactly the kind of translation task
`docs/architecture.md`'s "vagueness stops at the human boundary" section
sanctions the LLM for.

`split` is a closed `Literal`, not a `float`, for the identical reason
`UniverseFilter.cut` in `Charter` is a closed `Literal` and not a raw
percentile: `.claude/rules/data-pipeline.md` requires thresholds to be
relative and never hand-picked, and a free-floating fraction is exactly
the kind of number an LLM could quietly drift toward whatever makes a
result look better, or simply produce inconsistently across runs (0.71
one time, 0.68 the next, with no way to tell if that variation means
anything). Constraining it to two named choices makes every design
auditable at a glance and removes an entire axis of run-to-run noise.

The pairing validator exists because `design_type` and `walk_forward_folds`
are not independent — a `walk_forward` design with no fold count is
underspecified (how many folds?), and a `simple_holdout` design that
somehow carries a fold count is a contradiction (why would there be folds
if there's no walk-forward?). Without this validator, either malformed
combination would flow silently into `study_design.py`'s branch on
`parsed.design_type`, and only the `simple_holdout` branch even reads
`walk_forward_folds` — so a malformed walk-forward design (missing fold
count) would crash later with a confusing `None * fraction`-style error
deep inside `_walk_forward_windows`, far from where the actual problem
originated. Catching it at the schema boundary means the failure message
names the actual defect immediately.

`walk_forward_folds: int | None = Field(default=None, ge=2)` — the lower
bound of 2 exists because "walk-forward with one fold" isn't walk-forward
at all, it's just a holdout split with an unnecessary field.

### `schemas.py` — `StudyDesign`

```python
class StudyDesign(BaseModel):
    parsed: ParsedStudyDesign
    in_sample: DateRange
    out_of_sample: DateRange
    walk_forward_windows: list[DateRange] | None = None
    null_hypothesis: str

    @model_validator(mode="after")
    def _check_no_overlap(self) -> "StudyDesign":
        if self.out_of_sample.start <= self.in_sample.end:
            raise ValueError(...)
        return self
```

This is the `ParsedX`/`X` split established by `Charter`/`ParsedCharter`
and `Hypothesis`/`ParsedHypothesis`, applied a third time. `in_sample`,
`out_of_sample`, and `walk_forward_windows` are never fields the LLM is
asked to produce — they're always computed in `study_design.py` from the
real cached price data, the same guarantee that means a hallucinated
ticker can never reach `Charter.resolved_universe` or a hallucinated
citation can never reach `Hypothesis.citations`. `null_hypothesis` is a
fixed string, not LLM output either — see the dedicated discussion in
section 3 ("the null hypothesis is a constant, not a field").

`_check_no_overlap` is a second line of defense, not the primary one: the
code that constructs a `StudyDesign` (`propose_study_design`) already
slices one ordered list of trading dates, so an inverted or overlapping
pair is not reachable through the normal path today. The validator exists
anyway because "not reachable today" is a much weaker guarantee than "not
representable at all" — if a future change to `propose_study_design` (or
a Component 6 change that constructs a `StudyDesign` a different way)
introduced an off-by-one, this validator turns that bug into an immediate,
loud `ValidationError` at construction time instead of a silently corrupt
pre-registered design that Component 6 would trust and act on.

### `hypothesis.py` — `hypothesis_from_row`

```python
def hypothesis_from_row(row: HypothesisRow) -> Hypothesis:
    return Hypothesis(
        parsed=ParsedHypothesis(
            rule=StrategyRule.model_validate(row.rule),
            prediction=row.prediction,
            falsification_condition=FalsificationCondition.model_validate(row.falsification_condition),
            rationale=row.rationale,
        ),
        grounding_tier=row.grounding_tier,
        citations=[GroundingChunk.model_validate(c) for c in row.citations],
    )
```

The inverse of the row-building `propose_hypothesis` already did inline at
the end of Component 4. This component needs to read a hypothesis's rule
and rationale back out of its JSONB storage in order to build the design
prompt; Component 6's execution loop will need the identical
reconstruction to actually run the backtest against the rule. Putting it
here, once, next to `HypothesisRow` and `Hypothesis`, means both callers
share one reconstruction path rather than two copies that could drift out
of sync (for instance, if `ParsedHypothesis` ever gained a field, only one
function would need updating instead of two).

### `study_design.py` — module-level constants

```python
SPLIT_TO_FRACTION = {"70/30": 0.7, "80/20": 0.8}
```
The literal mirror of `charter.py`'s `CUT_TO_PERCENTILE`. This dict is the
*only* place a `split` name becomes an actual number — everywhere else in
the codebase only ever sees the name.

```python
MIN_WINDOW_TRADING_DAYS = 20
```
A sanity floor, not a statistical threshold. It exists purely to catch a
degenerate design — a window so short (a handful of trading days) that no
meaningful backtest could run against it — before it silently reaches
Component 6. Twenty trading days (roughly one calendar month) was chosen
as "obviously still too short to trust, but long enough that the guard
never fires on any realistic design," not as a number calibrated against
real results the way `LOCAL_RELEVANCE_THRESHOLD` was in Component 3 —
there's no equivalent calibration exercise here because this isn't a
judgment call about correctness, it's a floor against nonsense.

```python
NULL_HYPOTHESIS = (
    "This rule's trade returns are not distinguishable from randomized "
    "entries at the same trade frequency (Stage 4's test_significance Monte "
    "Carlo permutation test)."
)
```
See section 3 for why this is a constant.

### `study_design.py` — `_common_price_bounds`

```python
def _common_price_bounds(session, tickers, history_start):
    rows = session.execute(
        select(PriceBar.ticker, func.min(PriceBar.date), func.max(PriceBar.date))
        .where(PriceBar.ticker.in_(tickers))
        .group_by(PriceBar.ticker)
    ).all()
    missing = set(tickers) - {ticker for ticker, _, _ in rows}
    if missing:
        raise InsufficientHistoryError(...)
    earliest = max(min_date for _, min_date, _ in rows)
    latest = min(max_date for _, _, max_date in rows)
    if history_start is not None:
        earliest = max(earliest, history_start)
    if earliest >= latest:
        raise InsufficientHistoryError(...)
    return earliest, latest
```

One `GROUP BY` query returns every universe ticker's own `MIN(date)` and
`MAX(date)`. `earliest` is the **maximum of the minimums** and `latest` is
the **minimum of the maximums** — the intersection of every ticker's
range, not the union. This is the single most important correctness
decision in this component; see section 3 for the full "why" and the real
bug class it prevents.

`missing` catches a ticker in `charter.resolved_universe` with literally
zero cached rows (shouldn't happen for a screened, confirmed charter, but
costs nothing to check and fails with a specific ticker list rather than a
confusing `max() arg is an empty sequence` from the next line). The final
`earliest >= latest` check catches the case where two tickers' ranges
don't overlap at all, or where `history_start` pushes the floor past the
common ceiling — both real possibilities for a universe with tickers of
very different listing dates.

### `study_design.py` — `_trading_dates`

```python
def _trading_dates(session, ticker, start, end):
    return list(session.execute(
        select(PriceBar.date)
        .where(PriceBar.ticker == ticker, PriceBar.date >= start, PriceBar.date <= end)
        .order_by(PriceBar.date)
    ).scalars())
```

Queries one representative ticker's actual cached dates within the common
window (rather than the union/intersection of every ticker), because US
equities all trade on the same exchange calendar — same weekends, same
market holidays. Any one ticker's dates within `[start, end]` are every
ticker's dates. This avoids an O(universe size) query for zero additional
correctness, and — more importantly — this list of *real* dates, not a
calendar-day count, is what the split arithmetic slices. Splitting by
elapsed calendar days instead (e.g. "70% of the days between date A and
date B") would give a *slightly* wrong in-sample proportion whenever
holidays cluster unevenly across the window; splitting by the actual
trading-date list sidesteps that entirely.

### `study_design.py` — the split arithmetic

```python
def _holdout_split_index(trading_dates, fraction):
    idx = round(len(trading_dates) * fraction)
    return max(1, min(idx, len(trading_dates) - 1))
```
Converts a fraction into an array index, clamped so the index can never
produce a zero-length side even for a pathologically small `trading_dates`
list (that pathological case is still caught downstream by
`_check_window_lengths`, but the clamp keeps the arithmetic itself
well-defined rather than producing a negative-length slice).

```python
def _simple_holdout(trading_dates, fraction):
    idx = _holdout_split_index(trading_dates, fraction)
    _check_window_lengths(idx, len(trading_dates) - idx)
    return (
        DateRange(start=trading_dates[0], end=trading_dates[idx - 1]),
        DateRange(start=trading_dates[idx], end=trading_dates[-1]),
    )
```
Slices the trading-date list at `idx`: everything before is in-sample,
everything from `idx` onward is out-of-sample. `trading_dates[idx - 1]`
and `trading_dates[idx]` are adjacent trading days by construction, so
there is no gap and no overlap between the two windows — this is a
structural guarantee from how Python list slicing works, not something
that needs a separate check (though `StudyDesign`'s own validator still
checks it, per the "not reachable today ≠ not representable" reasoning
above).

```python
def _fold_boundaries(trading_dates, n_folds):
    fold_size = len(trading_dates) // n_folds
    _check_window_lengths(*([fold_size] * (n_folds - 1) + [len(trading_dates) - fold_size * (n_folds - 1)]))
    folds = []
    for i in range(n_folds):
        start_idx = i * fold_size
        end_idx = (start_idx + fold_size - 1) if i < n_folds - 1 else len(trading_dates) - 1
        folds.append(DateRange(start=trading_dates[start_idx], end=trading_dates[end_idx]))
    return folds
```
Partitions a date list into `n_folds` consecutive chunks. Integer division
means `n_folds` may not divide the list evenly; the **last** fold absorbs
the remainder (`end_idx = len(trading_dates) - 1` on the final iteration)
rather than, say, distributing the remainder across all folds or dropping
it. This guarantees every trading date in the input list ends up in
*exactly one* fold — no date is silently dropped from the study, which
matters because a dropped date near a fold boundary would be a real,
silent gap in the tested history. The length check accounts for the
uneven last fold explicitly (`len(trading_dates) - fold_size * (n_folds - 1)`
is the true final-fold size, not `fold_size`), so the guard can't be
fooled by a last fold that happens to be short even when every other fold
is comfortably long.

```python
def _walk_forward_windows(trading_dates, fraction, n_folds):
    idx = _holdout_split_index(trading_dates, fraction)
    _check_window_lengths(idx)
    in_sample = DateRange(start=trading_dates[0], end=trading_dates[idx - 1])
    oos_folds = _fold_boundaries(trading_dates[idx:], n_folds)
    return [in_sample, *oos_folds]
```
Composes the two primitives above: `fraction` first carves off one
continuous in-sample period (identical math to `_simple_holdout`'s first
half), then the *remaining* trading dates get folded into `n_folds`
out-of-sample chunks. This is the key design choice that makes `split`
mean the same thing in both `design_type` branches — see section 3.

### `study_design.py` — `_study_design_prompt` and `propose_study_design`

The prompt hands the LLM the rule (as JSON, so it can see the exact
entry/exit logic), the prediction, the rationale, the falsification
condition, the universe size, and — critically — the real trading-day
count computed from the actual database, so the LLM's fold-count choice
is grounded in real data volume rather than a guess. `propose_study_design`
itself: loads the hypothesis and its parent charter, computes the real
common bounds and trading-date list (one `with SessionFactory()` block,
closed before the LLM call — the same pattern `charter.py` and
`hypothesis.py` use, so a slow LLM call never holds a database connection
open), calls `structured_output`, branches on `parsed.design_type` into
whichever windowing function applies, builds the final `StudyDesign`, and
persists it in a second, separate session block.

---

## 3. Design decisions and rejected alternatives

### Intersection, not union, for the universe's common price bounds

**Chosen:** `_common_price_bounds` takes the *latest* of every ticker's
earliest date, and the *earliest* of every ticker's latest date — i.e.
the date range that is valid for **every** ticker in the universe
simultaneously.

**Alternative considered:** take the *union* — the earliest date any
ticker has, through the latest date any ticker has — and let individual
`run_backtest` calls in Component 6 simply run out of data early for
shorter-lived tickers.

**Why rejected:** this would silently give different tickers different
effective study lengths. Imagine a five-ticker universe where four have
been trading since 2010 and one IPO'd in 2021. Under the union approach,
the design's out-of-sample window might span 2023–2026 for every ticker
on paper, but the 2021 IPO's backtest would only actually have ~2
in-sample years to work with versus ~13 for the others. If that newer
ticker happens to perform differently, there is no way to tell from the
verdict whether that's a real effect or simply an artifact of it having
less history, a shorter warm-up period for slow indicators (this
project's own live-verified hypothesis uses EMA200, which needs ~200 bars
of warm-up before it produces a value at all), or a different macro period
than its peers. That's exactly the failure `docs/architecture.md` Step 3
is naming when it says "a cross-sectional claim tests the universe
together" — testing the universe *together* requires one shared window,
not five windows that happen to share a label. The intersection approach
makes this failure structurally impossible instead of relying on someone
noticing it later in a verdict that looks statistically fine on its
surface.

**Cost to reverse:** low in code (swap `max`/`min` for `min`/`max`), but
reversing it would reintroduce a real bug silently — nothing downstream
would flag it, because every individual number returned by
`run_backtest`/`test_significance` would still look perfectly valid in
isolation. This is a load-bearing decision, not a cosmetic one.

### `split` means "in-sample proportion of the total span" in both design types

**Chosen:** for `walk_forward`, `split`'s fraction still carves off one
continuous in-sample period from the *whole* window first; only the
remainder gets divided into `walk_forward_folds` out-of-sample chunks.

**Alternative considered:** let `split` apply *inside each fold*
independently — e.g. for `walk_forward_folds=3`, produce three
in-sample/out-of-sample pairs, each internally split 70/30, rolling
forward.

**Why rejected:** that's classic walk-forward *optimization* — re-fit a
model's parameters on each fold's in-sample slice, test on that fold's
out-of-sample slice, roll forward. This system's `StrategyRule`s have no
fit step at all (a rule like "NATR < its own 20-period SMA" has no
parameter that gets tuned to data); there is nothing to re-optimize per
fold. Building that alternative would mean inventing a re-fitting
mechanism this system doesn't have and doesn't need, purely to make
`split` behave symmetrically across both branches — complexity added for
consistency's own sake, not because the domain calls for it. The chosen
design keeps `split`'s meaning identical in both branches (a single,
global in-sample/out-of-sample proportion) and gives `walk_forward_folds`
exactly one job (how finely to slice the out-of-sample remainder for the
decay check) — no field does double duty, and no field goes unused in
either branch.

**Cost to reverse:** moderate — would require a genuinely different
`_walk_forward_windows` implementation and would only make sense once (if
ever) this system's rules gained tunable, fittable parameters. Not
expected to be needed before then.

### `regime_split` is named as a deferred gap, not built

**Chosen:** only `simple_holdout` and `walk_forward` exist as
`design_type` options. A third type — splitting by `classify_regime`
labels (trending/choppy, high/low volatility) instead of by calendar date
— is explicitly not implemented.

**Why rejected (for now):** `docs/architecture.md` Step 3 names
regime-dependence as one motivating example for "different hypotheses
need different experiments," but none of Component 4's six `EffectFamily`
values (momentum, mean_reversion, low_volatility, value, quality,
seasonality) inherently produces a regime-dependence *claim* the way a
persistence claim naturally falls out of a rationale that says "this
anomaly may have been arbitraged away." Building `regime_split` now would
mean designing and testing an entire second splitting axis — partition by
`classify_regime`'s labels rather than by contiguous date ranges, which is
a structurally different operation, not a variant of the date-window
arithmetic already built — against hypothetical hypotheses rather than
real ones. That's exactly the "designing for a hypothetical future
requirement" CLAUDE.md's working agreement warns against. It's recorded
here as a named, deliberate gap rather than a silent omission so it's
easy to pick up if Component 4 ever generates a hypothesis that actually
needs it.

**Cost to reverse:** moderate-to-high — would need a new
`ParsedStudyDesign.design_type` option, a new field shape (regime labels
aren't date ranges), and a query against `classify_regime`'s output rather
than `PriceBar`'s date range. Deferred, not blocked; nothing in the
current schema makes this harder to add later.

### The mandatory control has no field, no flag, no on/off switch

**Chosen:** `StudyDesign` has no `control_required`, `run_control`, or
similarly named field anywhere. The requirement that Component 6 always
run Stage 4's `test_significance` (the "beat randomized entries at the
same trade frequency" comparison `docs/architecture.md` calls mandatory)
is never represented as data at all — it's an invariant that Component
6's execution loop graph will enforce structurally, the same way "the LLM
can only choose from tools that actually exist" is a loop guardrail, not
a configurable option.

**Alternative considered:** a boolean field on `ParsedStudyDesign`, either
LLM-set or hardcoded `True`, that Component 6 checks before deciding
whether to run the control.

**Why rejected:** if it's LLM-set, that's a direct, concrete instance of
the exact failure mode `.claude/rules/agent-honesty.md` names — "LLMs are
agreeable... left alone, one looking at Sharpe 0.21 writes 'shows modest
promise'" — except one layer earlier: instead of softening a bad verdict,
an agreeable model could simply skip the check that would have produced
the bad verdict in the first place, by setting `control_required=False`
on a design where the control was inconvenient. Even hardcoding it to
`True` and never letting the LLM touch it is worse than not having the
field: a field that always reads `True` in every real row is dead weight
that invites a future "just make it configurable" change from someone who
doesn't know why it was pinned. Leaving it out of the schema entirely
means there is no data anywhere that could be edited, defaulted, or
migrated into skipping the control — the only way to remove the guarantee
is to change Component 6's actual code path, which is a much higher bar
than flipping a stored value.

**Cost to reverse:** deliberately high. This is the one decision in this
component that should be hard to walk back by accident.

### The null hypothesis is a fixed constant, not computed or LLM-written per design

**Chosen:** `NULL_HYPOTHESIS` is one literal string, reused verbatim in
every `StudyDesign`.

**Alternatives considered:** (a) have the LLM write a natural-language
null hypothesis alongside `rationale`, for readability in a future
frontend; (b) have code assemble a templated string that references the
specific falsification condition or ticker count per design.

**Why rejected:** (a) is a hallucination surface with no corresponding
benefit — the null being tested is *always* the same thing (Stage 4's
`test_significance` always tests trade returns against randomized entries
at the same frequency; there is no version of this system where a
hypothesis gets a different null), so asking the LLM to phrase it fresh
each time only creates chances for it to phrase it *wrong* — e.g.
describing a null that sounds tailored to the hypothesis but doesn't
actually match what `test_significance` computes — for zero informational
gain. (b) was considered and rejected as needless complexity: the
falsification condition and ticker count are already visible elsewhere in
the design and the hypothesis, so restating them inside `null_hypothesis`
would be redundant, not clarifying, and would require keeping a template
in sync with fields it's derived from for no benefit.

**Cost to reverse:** trivial either way; this was a "no reason to" call,
not a load-bearing one.

### Deterministic date-window functions are private (`_`-prefixed) and unit-tested directly

**Chosen:** `_common_price_bounds`, `_trading_dates`,
`_holdout_split_index`, `_simple_holdout`, `_fold_boundaries`, and
`_walk_forward_windows` are all module-private, and the test suite imports
and calls them directly rather than only testing the public
`propose_study_design` entry point.

**Alternative considered:** test only `propose_study_design` end-to-end,
treating the window arithmetic as an implementation detail.

**Why rejected:** `propose_study_design` requires a live LLM call with no
mocking layer in this codebase yet (`llm_client.structured_output` talks
to real Bedrock; there is no fake/injectable client). Testing only through
that entry point would mean either the formal automated suite makes real,
non-deterministic network calls on every run (slow, costly, and — because
the LLM's `design_type` choice isn't guaranteed reproducible — flaky by
construction), or the date arithmetic goes completely uncovered by
automated tests, relying only on the two live runs performed once during
this component's own build. Testing the private functions directly gets
full, fast, deterministic coverage of every real correctness property
(no-gap/no-overlap partitioning, intersection-not-union bounds, the
minimum-window floor) without needing the LLM at all — which mirrors
exactly how `test_grounding.py` tests `retrieve_local` directly rather
than only through `ground_topic`'s full escalation path.

**Cost to reverse:** low; this is a testing-strategy choice, not an
architectural one.

---

## 4. Concepts introduced

**In-sample / out-of-sample split.** A backtest's whole point is to
estimate whether a rule would have made money on data the rule wasn't
"aware of." If a single continuous history is used both to notice a
pattern and to confirm it works, the confirmation is worthless — anything
that happened to work in that specific stretch of history will look good,
whether or not it reflects a real, repeatable edge. Splitting the history
into an earlier in-sample period and a later out-of-sample period means
the out-of-sample number is evidence about a period the rule's own design
process (here, the LLM proposing the rule in Component 4) never saw. This
project doesn't currently *use* the in-sample period to tune anything —
these are fixed rules, not fit models — so the in-sample window's role
right now is closer to "the baseline first look," with the out-of-sample
window the actual held-out check. What goes wrong without this: a rule
that happens to fit one historical accident (say, a single large AAPL
rally) gets reported as evidence of a real anomaly, because nothing ever
tested it against unseen data.

**Walk-forward testing.** A stronger version of the same idea, applied
repeatedly: instead of one in-sample/out-of-sample split, the total
history is divided into several consecutive out-of-sample folds, and the
same fixed rule is checked against each one in chronological order. The
question it answers is different from a single split's question — not
"does this beat random once," but "does this keep beating random across
multiple distinct stretches of time, or did it only work in one of them
and fail in the others." This matters specifically for decay claims: an
anomaly that was real in 2010–2015 and has since been arbitraged away by
other market participants finding the same edge would show strong
performance in an early fold and weak-to-negative performance in later
folds — a pattern a single 70/30 split covering the whole history could
easily average away into a mediocre-but-not-obviously-dead overall
number. What goes wrong without it: a genuinely decayed edge gets
reported as "still works, modestly," because the one out-of-sample window
happens to straddle both the good years and the bad ones.

**Cross-sectional intersection vs. union (the specific bug class this
component's biggest decision prevents).** When a claim is about a whole
universe of tickers together, "the same test" only means something if
every ticker in that test covers the same calendar period. Taking the
union of every ticker's available history (as broad as possible) sounds
more data-generous, but it silently lets different tickers run for
different effective lengths, which reintroduces the exact kind of
uncontrolled variable a designed experiment exists to remove — same
category of problem as the survivorship-bias and lookahead-bias concerns
already documented in `.claude/rules/data-pipeline.md` and
`.claude/rules/backtesting-rigor.md`, just at the study-design layer
instead of the data-ingestion or backtest-execution layers.

---

## 5. How this component was verified

**Formal automated tests** (`tests/agentic_core/test_study_design.py`,
14 tests, all passing; full suite 235/235 passing after this component):

- `_common_price_bounds` tested against two real DB-backed tickers with
  deliberately different, overlapping-but-not-identical date ranges,
  confirming the result is the intersection (not the union), that
  `history_start` correctly raises the floor, that a missing ticker raises
  `InsufficientHistoryError` by name, and that two non-overlapping tickers
  raise as well.
- `_trading_dates` confirmed to return the correct sorted sub-list.
- `_simple_holdout` confirmed to split at the correct fraction with no gap
  (the out-of-sample start is exactly one trading day after the in-sample
  end) and to raise below the minimum window.
- `_fold_boundaries` confirmed, via an explicit coverage check, to
  partition its input into folds that cover every input date exactly once
  with no gaps and no overlaps between consecutive folds — and to raise
  when a fold would be too short.
- `_walk_forward_windows` confirmed to produce a first window identical to
  what `_simple_holdout` alone would produce, and confirmed (again via
  explicit coverage) that its out-of-sample folds partition exactly the
  remainder after the in-sample window, with no gaps or overlaps.
- All three new schema validators (`DateRange` ordering, `ParsedStudyDesign`
  design-type/fold pairing in both directions, `StudyDesign` no-overlap)
  confirmed to reject the malformed case they exist to catch.

**What the automated suite does not cover:** the LLM call itself.
`structured_output` has no mock in this codebase (a deliberate absence —
see `llm_client`'s own module docstring on why retry/mocking is deferred
to Stage 5's loop guardrails), so `_study_design_prompt` and the
`design_type`/`split`/`walk_forward_folds` choice the LLM actually makes
are verified live instead, matching the bar Components 2 and 4 already
set (their own commit-log entries describe "four live Bedrock calls, not
mocked" and similar).

**Live verification, both branches:**

1. Against the real, already-confirmed low-volatility AAPL hypothesis
   left over from Component 4's own verification
   (`0d168ccd-9fc9-4e92-b934-b190046d4603`, universe = `['AAPL']`, ~4,164
   cached trading days from 2010-01-04 to 2026-07-24): a real
   `propose_study_design` call correctly produced `design_type:
   simple_holdout`, `split: 80/20`, with the LLM's own rationale
   correctly reasoning that the rule's EMA(200) needs roughly 200 bars of
   warm-up and choosing the larger in-sample share specifically to
   accommodate that — a real, sound piece of reasoning about *this*
   rule's own mechanics, not a generic default. The resulting design
   (`in_sample`: 2010-01-04 → 2023-03-28; `out_of_sample`: 2023-03-29 →
   2026-07-24) was persisted, then re-read directly from the database in
   a fresh query (not trusted from the script's own printed output) to
   confirm it matched exactly.
2. Against a temporary hypothesis constructed specifically to state a
   persistence/decay claim in its rationale ("does the low-volatility
   anomaly still hold in recent years, or has it decayed/been arbitraged
   away... McLean & Pontiff 2016... need to check whether performance is
   stable across multiple distinct chronological sub-periods"): a real
   `propose_study_design` call correctly produced `design_type:
   walk_forward`, `split: 80/20`, `walk_forward_folds: 5`, yielding six
   total windows (one in-sample, five out-of-sample folds) with real,
   verified-adjacent boundaries and no gaps. This is real evidence the
   LLM is actually distinguishing the two design types by their content,
   not defaulting to one regardless of input — the same "prove it in both
   directions" bar Component 4's dedup logic was held to (a real repeat
   caught, a genuinely different rule not flagged).

**Database cleanliness, checked directly, not assumed:** after both live
runs, every `agentic_core` table in the real `strategy_research` database
was queried directly. `charters`: 6 rows, all pre-existing. `hypotheses`:
1 row — the real Component 4 hypothesis, timestamped before this
component's work began; the temporary decay-framed hypothesis used for
verification run 2 was deleted in a `finally` block and confirmed absent.
`study_designs`: 1 row — the single legitimate result from verification
run 1, timestamp matching. `study_runs`, `tool_call_traces`, `verdicts`,
`scoreboard_entries`: all 0, untouched, as expected since Components 6–7
don't exist yet. The 14 formal tests never touch this database at all —
`db_session` (from the root `conftest.py`) points at the entirely separate
`strategy_research_test` database, truncated per test.

**What this verification does not prove:** it does not prove the LLM will
always draw the `simple_holdout`/`walk_forward` line correctly on every
possible hypothesis — two data points (one clearly one type, one clearly
the other) establish that the distinction *can* be drawn correctly, not
that it always will be. An ambiguous hypothesis, phrased in a way that
could plausibly read either way, has not been tested. That's an honest
gap, not a hidden one — see section 6.

---

## 6. Interview defense

**"Why compute the intersection of the universe's price history instead
of just using each ticker's own full history?"** Because "test the
universe together" only means something if every ticker in the test
shares the same calendar window. Using each ticker's own full history
would let a newly-listed ticker get a much shorter effective study than
an established one, silently confounding "this ticker performs
differently" with "this ticker simply has less history" or "this ticker's
window happens to land in a different macro period." The intersection is
strictly more conservative — it costs some data at the edges — but it's
the only version of "point-in-time" and "same experiment for everyone"
that doesn't quietly reintroduce a hidden variable.

**"Why is there no field anywhere that could turn off the significance
control?"** Because the control isn't a design decision — it's
unconditional, per `docs/architecture.md`'s "the control is MANDATORY."
A stored boolean, even one hardcoded to `True`, is still *data* that could
be flipped, defaulted, or migrated later, either by a future change or by
an agreeable LLM finding a way to set it if it were ever exposed as an
LLM-settable field. Making the guarantee live only in Component 6's actual
code path — not in any row anyone could edit — means the only way to
remove it is to change code, which is a fundamentally higher bar than
changing data.

**Hard question: "You only tested the design_type classifier on two
hypotheses — one obviously a plain claim, one obviously a decay claim.
How do you know it won't default to the wrong type on something
ambiguous, and why is that an acceptable gap to ship with?"** Honest
answer: it isn't fully known, and that's a real, acknowledged limitation
of this component as it stands. The two live tests prove the LLM *can*
draw the distinction correctly when the signal is clear — they don't
prove it draws the line correctly near the boundary. The mitigating
factors are: (1) the cost of getting it wrong is bounded, not silent — a
hypothesis that should have gotten `walk_forward` but got `simple_holdout`
still gets a valid, honest holdout test; it just doesn't get the
additional decay-specific check, which is a missed opportunity for a
stronger verdict, not a wrong one. (2) Nothing about the design is
irreversible — Step 7 of `docs/architecture.md` already anticipates
re-testing a hypothesis under a new `StudyDesign`, so a wrongly-classified
hypothesis can be re-designed later. (3) This is exactly the kind of gap
Stage 6's golden-set evaluation is built to catch systematically instead
of by spot-check — a planted hypothesis with an ambiguous but genuinely
decay-flavored rationale is a natural addition to that golden set. Saying
"this is a known, bounded gap that a later stage is designed to catch" is
a stronger answer than claiming the two live tests are sufficient
coverage, which they are not.

**"Why not let the LLM propose the actual calendar dates directly, rather
than a fraction and a fold count?"** Two independent reasons. First,
dates require arithmetic against data the LLM hasn't seen — it has no
access to "AAPL's cached history actually starts 2010-01-04 and ends
2026-07-24," so any date it proposed would be a guess dressed up as a
fact, exactly the kind of ungrounded quantitative claim
`.claude/rules/agent-honesty.md` exists to prevent. Second, even if it
somehow guessed correctly, letting a model directly control the numbers
that end up in a *pre-registered* record removes the safety property
pre-registration is supposed to provide: a category (`simple_holdout` vs
`walk_forward`) plus a closed-vocabulary split leaves no room for a
subtly-tuned date range to sneak in — the actual boundary dates are
mechanically determined from real data, not chosen.

---

## 7. What comes next and why

Component 6 is the execution loop: LangGraph's `decide_next_action` /
`execute_tool` cycle that actually spends this `StudyDesign` — calling
`run_backtest` and `test_significance` with `in_sample`'s dates first,
then (only after that phase concludes) `out_of_sample`'s dates, and for a
`walk_forward` design, stepping through `walk_forward_windows` in order.
This is where "code enforces the data split, not the prompt" gets its
teeth: the loop's own state machine — not a prompt instruction — has to
make it structurally impossible to call a tool with an out-of-sample date
before the in-sample phase has actually run. It's also where the
mandatory control becomes real: the loop's node graph must always include
a `test_significance` call, with no path through the graph that skips it.

If this component were subtly wrong — say, the intersection bug were
actually a union bug — the failure wouldn't show up here. It would show
up downstream, in Component 6, as a cross-sectional backtest quietly
running different tickers over different effective lengths, producing
individually-valid-looking numbers that add up to a confounded
comparison. That's exactly why the test suite checks the intersection
property directly and explicitly, rather than waiting for it to surface
as a confusing pattern in some future multi-ticker verdict.
