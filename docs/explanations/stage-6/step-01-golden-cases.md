# Step 1 — Component 1, the Golden-Set Fixtures

## 1. What this does

`src/eval/fixtures.py` and `src/eval/golden_cases.py` are the first piece
of Stage 6, the evaluation harness. Stage 5 closed with Sacred Gate 2
proven *possible*: one real rejected walk-forward study, one deliberately
engineered confirmed study (`GATE5PROBE`, Component 8's own gate fixture).
`docs/explanations/stage-5/stage-5-summary.md` names the resulting gap by
its own hand: "you have exactly one real confirmed hypothesis and it's
synthetic... that gap has a name and an owner — Stage 6's golden set."
`docs/architecture.md` Section 9 specifies what that golden set is: a
fixed collection of hypotheses whose correct verdict is known in advance —
planted false (the agent must kill them), planted true (the agent must
confirm them), and known-caveat (the honest answer is "insufficient
evidence") — scored automatically, run on every future change to the
agentic core and continuously in production, so a planted-false hypothesis
starting to pass becomes this project's drift alarm.

This component builds the six fixtures that golden set consists of: two
planted-true, three planted-false, one known-caveat. Each is a fully
deterministic, hand-seeded price series plus a hand-built `Charter` /
`Hypothesis` / `StudyDesign` row in the real database, with its correct
verdict status declared in code before the fixture is ever run through
anything that could produce a verdict.

**What this is not.** It is not the harness itself — nothing here yet
drives these fixtures through the real execution loop
(`agentic_core.loop_graph`) or `agentic_core.verdict.render_verdict`, and
nothing here scores a result against the declared expectation. That is
Component 2 (`src/eval/harness.py`). It is not Stage 6's own gate proof
(`scripts/verify_stage6_gate.py`), which deliberately breaks the agent and
confirms the harness notices. And it is not a claim that these fixtures
resemble real trading hypotheses or that hypothesis generation itself
(Components 2–3's own LLM calls) works correctly — that is explicitly out
of scope, for reasons Section 3 explains.

New: `src/eval/__init__.py`, `src/eval/fixtures.py`,
`src/eval/golden_cases.py`.

---

## 2. Every meaningful line explained

### `fixtures.py` — the reusable half

```python
def build_cyclical_series(n_signals, dip_pct, rally_pct, noise_std, seed, start, noise_bars=8) -> pd.DataFrame:
    ...
    for i in range(1, n_bars):
        pos = i % cycle_len
        prev = closes[i - 1]
        if pos == noise_bars:       closes[i] = prev * (1 - dip_pct)
        elif pos == noise_bars + 1: closes[i] = prev
        elif pos == noise_bars + 2: closes[i] = prev * (1 + rally_pct)
        elif pos == noise_bars + 3: closes[i] = prev
        else:                       closes[i] = prev * (1 + rng.normal(0, noise_std))
```

This is `verify_stage5_gate.py`'s own `build_probe_series`, generalized:
the dip magnitude, rally magnitude, noise level, and cycle count are now
function arguments instead of module constants, because Stage 6 needs six
differently-shaped series from one generator, not one. The shape itself —
dip, one-bar settle, rally, one-bar settle, repeat — is unchanged, and it
is unchanged for a specific, hard-won reason: `docs/explanations/stage-5/
step-10-gate-script.md` records that an earlier version without the
settle bars produced *negative* Sharpe on an obviously-profitable-looking
pattern, because `backtesting.py` fills orders at the next bar's open, not
the signal bar's close — a dip immediately followed by a rally with no
settle bar meant every entry filled *after* the rally had already
happened. The settle bars exist so that whatever price level a signal
fires against is still the price the *next* bar opens at. Reusing this
exact mechanic rather than re-deriving fixture timing from scratch is a
direct application of evidence this project already paid for.

```python
def build_random_walk(n_bars, noise_std, seed, start) -> pd.DataFrame:
    ...
    closes[i] = closes[i - 1] * (1 + rng.normal(0, noise_std))
```

A separate generator, not a zero-magnitude call to `build_cyclical_series`
(`dip_pct=0, rally_pct=0`). Reusing the cyclical generator with zeroed
parameters would work numerically, but it would read as "a signal was
planted at 0% magnitude" rather than "no signal exists in this series at
all" — the distinction matters because `golden_false_no_edge` (Section 2
below) is specifically the "there was never an edge" case, and its own
code should say that plainly rather than imply a degenerate case of the
cyclical design.

```python
def seed_price_bars(session, ticker, series) -> None:
    ...
    price = Decimal(str(round(row["close"], 6)))
    session.add(PriceBar(ticker=ticker, date=row["date"],
        raw_open=price, raw_high=price, raw_low=price, raw_close=price, raw_volume=1_000_000,
        adj_open=price, ...))
```

Copied unchanged from `verify_stage5_gate.py`: every OHLC field set to the
same synthetic close, because these fixtures have no real corporate
action history to distinguish raw from adjusted prices — that distinction
(`.claude/rules/data-pipeline.md`'s own raw-vs-adjusted discipline) exists
to handle splits and dividends on *real* tickers, and a synthetic fixture
has nothing to adjust.

```python
def build_charter_and_hypothesis(ticker, rule, prediction, falsification_condition,
                                  rationale, grounding_tier, as_of_date) -> tuple[str, str, Charter, Hypothesis]:
```

Generalized from `verify_stage5_gate.py`'s own function of the same name,
which hardcoded one ticker and one rule. Six call sites need six different
rules, tickers, predictions, and — critically, see Section 3 — not
uniformly the same `grounding_tier`. Everything Components 2 and 4 would
normally produce by parsing a human mandate and grounding a hypothesis
against literature is constructed directly here instead; see Section 3
for why that is a deliberate scope boundary rather than a shortcut taken
without noticing it.

```python
def build_study_design(hyp_id, in_sample, out_of_sample, split="70/30") -> tuple[str, StudyDesign]:
    design = StudyDesign(parsed=ParsedStudyDesign(design_type="simple_holdout", split=split, ...), ...)
```

Always `simple_holdout`, never `walk_forward`. Every golden case needs
exactly one in-sample window (reported, never decisive —
`agentic_core.verdict.decide_status`'s own docstring) and one
out-of-sample window (the one that is decisive) to exercise the loop and
verdict end to end. Walk-forward's extra folds would test whether
Component 5's `propose_study_design` chose the right shape for a given
hypothesis — already Component 5's own tested concern — not whether
execution and verdict-rendering behave correctly, which is what Stage 6
is chartered to test.

```python
def cleanup(ticker, charter_id, hyp_id) -> None: ...
def verify_cleanup(ticker, charter_id, hyp_id) -> tuple[bool, str]: ...
```

Same deletion order and the same "verify by querying, not by trusting the
deletion calls succeeded" discipline as `verify_stage5_gate.py`. The one
change: `verify_cleanup` *returns* `(ok, detail)` instead of calling that
script's own `record()`-and-print helper, because reporting belongs to
the harness (Component 2), not to fixture construction.

### `golden_cases.py` — the six cases

```python
_FALSIFICATION = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.5)
```

One shared condition for all six cases, not six different ones. Varying
it per case would introduce a second variable into what each case tests —
the question this golden set asks is "does the system reach the
conclusion *this* condition mechanically implies," and holding the
condition fixed keeps that question uncontaminated by also asking "did I
pick a sensible condition."

```python
def _dip_rally_rule(name, drop_frac, rise_frac) -> StrategyRule:
    entry=_leaf(PriceTerm(field="close"), "lt", ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1 - drop_frac))
    exit=_leaf(PriceTerm(field="close"), "gt", ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1 + rise_frac))
```

`GATE5PROBE`'s own rule shape, generalized to a named helper because three
of the six cases below are built from it or its exact mirror image
(`golden_true_1`, `golden_true_2`, `golden_caveat_thin_sample`). The
`ScaledTerm(offset=-1)` wrapping — a *relative* comparison against
yesterday's close, not a fixed absolute price — is required because these
series drift over time as noise compounds; a constant threshold would not
reliably separate "ordinary noise" from "an engineered event" across the
whole series, exactly as `step-10-gate-script.md` already established for
the same rule shape.

```python
class GoldenCase(BaseModel):
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
```

One object carries everything two later consumers need: `loop_graph.
build_graph`/`initial_state` need `design_id`, `hypothesis_id`, and the
`charter`/`hypothesis`/`design` objects themselves; `fixtures.cleanup`
needs `ticker`, `charter_id`, `hypothesis_id`; the harness's scoring needs
`expected_status` and (for one case) `expected_caveat_substring`. Keeping
all of it on one Pydantic model means the harness never has to
reconstruct fixture identity from a `study_run_id` after the loop
finishes, and a case can be built, run, scored, and torn down by passing
this one object through three functions.

### The six cases and their real, verified numbers

Every number below came from a direct call to `run_backtest()` and
`test_significance()` — the actual functions the MCP tools wrap, no LLM,
no database — on the exact series and rule each case uses, run *before*
any of it was wired into this file. This mirrors `GATE5PROBE`'s own
verify-by-execution discipline exactly, and it is what turned up all
three honesty notes below; none of them were found by reasoning about the
design on paper.

| Case | Trades | Sharpe | p-value | Tier | Which gate decides it |
|---|---|---|---|---|---|
| `golden_true_1` | 61 | 0.9318 | 0.0033 | `none` (threshold 0.005) | all three pass → confirmed |
| `golden_true_2` | 46 | 1.3061 | 0.0033 | `none` (threshold 0.005) | all three pass → confirmed |
| `golden_false_no_edge` | 85 | -0.8790 | 0.4518 | `none` | falsification **and** control fail → rejected |
| `golden_false_fails_control` | 62 | 0.7042 | 1.0000 | `none` | control alone fails → rejected |
| `golden_false_breaches_bar` | 26 | -4.6928 | 0.9967 | `none` | falsification (and control) fail → rejected |
| `golden_caveat_thin_sample` | 11 | 0.9519 | 0.0166 | `whitelist_search` (threshold 0.025) | sample-adequacy alone fails → inconclusive |

`golden_true_1` and `golden_true_2` are two *independently parametrized*
confirm fixtures (different dip/rally magnitudes, noise levels, signal
counts, and seeds — not the same design re-seeded), specifically so a
pass is two non-redundant data points rather than one, directly answering
`stage-5-summary.md`'s own "sample size of one" criticism of `GATE5PROBE`.

`golden_false_no_edge` is a pure random walk with an entry that fires on
any down day and a fixed 3-bar hold — no data-dependent `exit`, so
`test_significance` uses the *probability-based* random control
(`make_random_entry_strategy`), not the anchored one the next case
exercises. There is genuinely no signal in the series for this rule to
have found, and it fails both gates decisively.

`golden_false_fails_control` is the case built specifically around what
the mandatory control exists to catch. It shares `golden_true_1`'s exact
profitable exit condition (`close > 1.10 × close[-1]`) but replaces the
carefully-timed dip entry with "buy on any down day" — an entry with no
real informational content. Because the exit is data-dependent,
`test_significance` uses the *anchored* control
(`make_anchored_random_entry_strategy`), which shares the real strategy's
own exit bars and randomizes only the entry point within the same
pre-exit window. The real strategy's raw Sharpe (0.7042) clears the naive
0.5 bar — it looks profitable, because every trade eventually rides a
genuine engineered rally — but its p-value is 1.0000, and the reason is
visible in the raw numbers: `null_mean_sharpe` was 0.8003, *higher* than
the real strategy's own 0.7042. Randomly-timed entries in the same window
did better on average than this rule's own entries. That is the control
working exactly as designed: a real, profitable-*looking* exit does not
make the *entry* skillful, and this is the one case in the set where a
naive "did it clear the bar" read gives the wrong answer.

`golden_false_breaches_bar` is the entry/exit-swapped mirror of
`golden_true_1`, run against the *identical* series (same seed, same
parameters) — buy the top of each engineered rally, sell the bottom of
the next engineered dip. It fails both gates by an overwhelming margin.

`golden_caveat_thin_sample` reuses `golden_true_1`'s exact rule shape
against the same cyclical design but with `n_signals` cut from 60 to 10,
so the out-of-sample window realizes only 11 trades — well under
`agentic_core.verdict.MIN_TRADES_FOR_CONFIRMATION` (30). Both other gates
pass; only sample-adequacy fails. Its `grounding_tier` differs from every
other case, and why is Section 3's first honesty note.

```python
GOLDEN_CASE_BUILDERS: list[Callable[[], GoldenCase]] = [
    build_golden_true_1, build_golden_true_2, build_golden_false_no_edge,
    build_golden_false_fails_control, build_golden_false_breaches_bar,
    build_golden_caveat_thin_sample,
]
```

The ordered list Component 2's harness will iterate. A plain list of
functions, not a registry or a plugin-discovery mechanism — six is not
enough cases to warrant either, and CLAUDE.md's own working agreement is
explicit about not designing for hypothetical future scale ("three
similar lines is better than a premature abstraction").

### What was skipped

Genuine boilerplate: the repeated `_seed(ticker, in_sample, out_of_sample)`
helper (three lines, a thin wrapper over `SessionFactory` +
`seed_price_bars` + `pd.concat`), and the per-case `prediction`/
`rationale` string literals, whose content is already covered by the
numbers table above and whose only real content — "verified sharpe X,
p-value Y" — is stated once there rather than six times.

---

## 3. Design decisions and rejected alternatives

### `fixtures.py` duplicates `verify_stage5_gate.py` rather than importing from it

**Chosen:** `seed_price_bars`, the charter/hypothesis/design builders, and
`cleanup`/`verify_cleanup` are written fresh in `src/eval/fixtures.py`,
even though they are direct generalizations of functions that already
exist, unchanged in shape, in `scripts/verify_stage5_gate.py`.

**Alternative considered:** refactor `verify_stage5_gate.py` to import
these helpers from the new shared module, eliminating the duplication
entirely.

**Why rejected:** that script's gate already passed, live, at real API
cost, and its passing result is a closed historical proof — the record
this project relies on for Sacred Gate 2's confirm-path claim. Editing
it, even a pure import-only change with no logic difference, means its
passing result is no longer *self-evidently* still true; trusting it
again without re-running it live would be assuming the refactor didn't
subtly change behavior, and re-running it to re-earn confidence costs
real money for evidence this project already has. The duplication this
creates is small and stable — DB scaffolding (a loop over price rows, two
cleanup queries), not business logic that could drift out of sync in a
way that matters — which is what makes this an acceptable, disclosed
exception to avoiding repetition rather than a violation of it.

**Cost to reverse:** low, but not worth paying. If `verify_stage5_gate.py`
is ever touched again for an unrelated reason, migrating it onto
`eval.fixtures` at that point would be free; touching it *solely* to
deduplicate is not.

### Hand-built `Charter`/`Hypothesis`/`StudyDesign` rows, skipping Components 2–4's own LLM calls

**Chosen:** every fixture constructs its `Charter`, `Hypothesis`, and
`StudyDesign` directly in Python — no call to `charter.parse_charter`,
`hypothesis.propose_hypothesis`, or `study_design.propose_study_design`.

**Alternative considered:** run each fixture's price data through the
real charter-confirmation and hypothesis-generation pipeline, the way a
genuine research mandate would.

**Why rejected:** those functions exist to translate a human's fuzzy
English or a literature search into structure — real, already-tested
functionality (Components 2–4's own live verification), but not what
Stage 6 is chartered to test. Routing golden-set fixtures through them
would add real LLM calls, real non-determinism, and real cost to
something whose entire value is being an unambiguous, repeatable
regression check, and it would conflate two very different failure
modes: "the system generated a bad hypothesis" (a fuzzy, hard-to-grade
property, Components 2–3's own concern) versus "the system executed a
known-good-or-bad hypothesis and reached the wrong conclusion" (a sharp,
gradeable property, and the actual thing Stage 6 exists to test at
scale). This is the same disclosed scope boundary Component 8's
`GATE5PROBE` fixture already established for exactly this reason.

**Cost to reverse:** high if ever wanted — it would mean rebuilding the
golden set on top of six real, confirmed mandates instead of six
hand-built ones, which reopens the after-the-fact-selection problem the
next decision addresses.

### Synthetic, seeded price series — never real cached market data

**Chosen:** every fixture uses fully synthetic, seeded price data for a
dedicated ticker, never a real ticker's cached history.

**Alternative considered:** use real market data already in the database
— for instance, the real AAPL walk-forward study Component 7's own test
suite already treats as ground truth — as one or more golden cases.

**Why rejected:** `.claude/rules/data-pipeline.md`'s own corporate-actions
discipline requires the pipeline to re-fetch splits and dividends weekly
and do a full re-fetch monthly — correct, required behavior for real
data. A golden case built on real cached prices would have its "known
truth" resting on data that can legitimately change *because the pipeline
is working correctly*, which would make the harness's own alarm
unreliable: a golden-set failure could mean "the agent regressed" or it
could mean "the cache refreshed and the numbers moved," and the harness
would have no way to distinguish them. That defeats the entire purpose of
a golden set as a *stable* drift detector for the agent specifically.
Synthetic, seeded, dedicated-ticker fixtures have no corporate-actions
history to refresh and are therefore permanently reproducible.

**Cost to reverse:** not applicable — this is the correct design for what
a golden set needs to be, not a placeholder standing in for something
better.

### Three planted-false cases, each isolating one of `decide_status`'s three gates

**Chosen:** `golden_false_no_edge`, `golden_false_fails_control`, and
`golden_false_breaches_bar` are built so each one's failure is
attributable to a specific, different mechanism — no edge at all,
failing the control specifically, and breaching the falsification bar
specifically (with `golden_false_no_edge` failing both, since a
genuinely edge-free series has no reason to survive either gate).

**Alternative considered:** build three cases that all simply "look bad"
by some informal standard — three variations on losing money — without
deliberately targeting different gates.

**Why rejected:** `decide_status` has three independent gates, and a
regression in any one of them (a threshold loosened, a comparison
direction flipped, a gate silently dropped from the check) is a
realistically different bug from a regression in another. Three cases
that all happen to fail through the same gate would give three-fold
redundant coverage of one failure mode and zero coverage of the other
two — exactly the outcome `docs/architecture.md`'s own emphasis on
`decide_status`'s multi-gate design would predict is a risk if fixtures
aren't built deliberately against it. Building one case per gate means a
regression anywhere in that function has a specific, minimal case that
will catch it.

**Cost to reverse:** none — this is additive; more cases per gate could
be added later without removing these.

### The grounding-tier deviation — found by the number, not reasoned out in advance

**Chosen:** `golden_caveat_thin_sample` uses `grounding_tier=
"whitelist_search"` (corrected threshold 0.025 at `hypothesis_count=1`).
Every other case in this file uses `grounding_tier="none"` (threshold
0.005).

**What was originally planned, and why it broke:** the first design used
`"none"` uniformly across all six cases, matching `GATE5PROBE`'s own
justification — the harshest multiple-comparisons tier proves a result
survives worst-case correction, so use it everywhere unless there's a
specific reason not to. Computing the real numbers (Section 2's table)
showed this fails for `golden_caveat_thin_sample` specifically: at
`tier="none"`, the corrected threshold is 0.005, and this case's real
p-value (0.0166) does **not** clear it. That means the fixture would fail
`mandatory_control` in addition to `sample_adequacy` — and
`agentic_core.verdict.decide_status` checks
`if not gate_falsification.passed or not gate_control.passed: return
"rejected"` **before** it ever looks at sample-adequacy. A case failing
both control and sample-adequacy renders as `rejected`, not
`inconclusive`, which would have collapsed this case's entire purpose:
isolating sample-adequacy as the *one* gate that fails.

**The fix, and the order events actually happened in, stated plainly:**
switching this one case to `grounding_tier="whitelist_search"` (threshold
0.025) gives a real, verified margin — 0.0166 clears 0.025 — so
sample-adequacy is this fixture's only failing gate. This choice is
retroactively well-justified on its own terms: a thin, ambiguous sample is
a genuinely different epistemic situation from a confidently wrong
result, and treating the two differently (a less punishing tier for a
case whose whole point is ambiguity from *volume*, not from weak evidence)
is defensible independent of how it was found. But it was **not** found
that way. It was found by computing the real threshold, discovering it
broke the case, and then fixing it — the justification was constructed
*afterward* to explain a result discovered empirically, and this document
says so in that order deliberately rather than presenting the good
argument as though it came first. Smoothing over that order would misrepresent
how this decision was actually made.

**Cost to reverse:** low mechanically (one string literal), but reversing
it without also changing the fixture's other parameters would silently
break the case again in the way just described — a future editor touching
this tier back to `"none"` needs to re-derive or re-check the threshold
math, which is exactly why this reasoning is recorded here rather than
left implicit in the code's own comment alone.

### The unexplained trade count on `golden_false_breaches_bar` — left open, by deliberate choice

**What was found:** a naive one-trade-per-engineered-cycle estimate on
`n_signals=60` predicts roughly 59 completed trades. The real, verified
number is 26.

**What was considered:** tracing the exact mechanism, the way
`step-10-gate-script.md`'s own v1/v2 fixture failures were fully
root-caused (there, the cause — `backtesting.py`'s next-bar-open fill
timing — was found and fixed before the fixture shipped). A plausible but
unverified guess here is that entry and exit signals sit adjacent within
the same 4-bar signal block (dip, hold, rally, hold), which could cause
some cycles' round trips not to close cleanly within the sampled window —
but this was not worked through.

**Why left open:** the verdict this case needs to produce is unaffected
either way. Both decisive gates fail by an overwhelming margin
regardless of the exact trade count — Sharpe -4.6928 against a 0.5 bar,
p-value 0.9967 against a 0.005 threshold — so the case is valid whether
the mechanism behind "26, not 59" is ever traced or not. This is a
deliberate decision, made explicitly rather than discovered as an
afterthought: chasing a mechanical curiosity that does not change a
fixture's correctness was judged not worth the time against the actual
harness work (Component 2) still ahead. If this number ever needs
explaining precisely — for instance, if a future case depends on trade
count matching a tighter estimate — the place to start is the same place
Component 8's own debugging started: `backtesting.py`'s order-fill
sequencing.

**Cost to reverse:** none currently owed — nothing depends on this number
being anything other than "large enough, and decisively negative."

### The ticker-length bug — a second, distinct layer of verification catching what the first could not

**What happened:** the numeric verification (Section 2's table) computed
every case's real `sharpe`/`p_value`/`num_trades` using an in-memory
OHLCV `DataFrame` passed directly to `run_backtest`/`test_significance` —
no database involved at all. That verification could not have caught a
database-level bug, by construction. A separate smoke test — building all
six fixtures against the real dev database via `eval.fixtures`, then
calling `cleanup` and `verify_cleanup` — found one: `PriceBar.ticker` is
`String(16)` (`data_pipeline/db/models.py`), and four of the six original
ticker names (`GOLDEN_FALSE_NO_EDGE`, `GOLDEN_FALSE_FAILS_CONTROL`,
`GOLDEN_FALSE_BREACHES_BAR`, `GOLDEN_CAVEAT_THIN_SAMPLE` — 20 to 26
characters) exceeded it and failed on insert with
`psycopg2.errors.StringDataRightTruncation`.

**Chosen fix:** shorten the four offending names to real-ticker-length
strings (`GOLD_NO_EDGE`, `GOLD_FAIL_CTRL`, `GOLD_BREACH_BAR`,
`GOLD_CAVEAT_THIN` — all ≤ 16 characters).

**Alternative considered:** widen `PriceBar.ticker`'s column definition to
accommodate longer synthetic names.

**Why rejected:** `String(16)` is a correct constraint for the column's
actual domain — real stock tickers are always short (`GATE5PROBE`, at 10
characters, already fit comfortably). The bug was in this fixture's
naming choice describing itself too verbosely, not in the schema's
assumption about what a ticker looks like. Widening a real schema
constraint to accommodate a synthetic fixture's naming preference would
be fixing the wrong side of the mismatch.

**Cost to reverse:** trivial — four string literals — and there is no
reason to.

---

## 4. Concepts introduced

**The anchored randomized-entry control, and what it isolates.**
`research_stats.significance.test_significance` picks between two control
strategies depending on whether a rule's exit condition is
data-dependent. When it is (as in `golden_true_1`, `golden_false_fails_
control`, and `golden_false_breaches_bar`), the control
(`make_anchored_random_entry_strategy`) reuses the real strategy's own
historical exit bars and randomizes *only* the entry point within the gap
before each one. This isolates a specific question — "does the real
strategy's chosen entry timing add value over an arbitrary entry timing
in the same window" — from a different, easier-to-satisfy one: "is this
rule profitable at all." `golden_false_fails_control` is the concrete
demonstration that these can disagree: raw Sharpe 0.7042 (profitable) but
p-value 1.0000 (that profit came entirely from the exit condition, which
any randomly-timed entry captures about as well).

**The resample floor.** `test_significance` runs `n_resamples=300`
Monte Carlo draws by default. The smallest non-trivial p-value the test
can produce is bounded below by that count (roughly `1/(n_resamples+1) ≈
0.0033`) — it cannot report a result more extreme than "zero of 300
randomized controls beat the real strategy," no matter how large the real
edge actually is. `golden_true_1` and `golden_true_2` both land exactly
on this floor (`p_value=0.0033`), which is the strongest signal the test
can produce at this resolution, not evidence of a weak result — but it
does mean the numeric margin against the strictest threshold (0.005) is
tighter in absolute terms (about 1.5×) than the margin on the
`false`-category cases, where the real p-values (0.4518, 1.0000, 0.9967)
are nowhere near any threshold this project's tiers produce.

**Multiple-comparisons correction as a real forcing function on fixture
design, not just on real hypotheses.** `.claude/rules/agent-honesty.md`
describes the grounding-tier correction (`TIER_SEARCH_BURDEN`) as a
defense against an agent generating enough real hypotheses that one
passes by chance. This component is the first place that mechanism
reached backward into *fixture design itself*: a fixture built to
isolate one failure mode (thin sample) was accidentally also tripping a
second, unrelated safety mechanism (the multiple-comparisons threshold),
because both mechanisms read the same `grounding_tier` field. The lesson
generalizes — two independently-justified safety checks that share an
input can interact in ways neither check's own design review would
surface, and the only way this one was caught was computing the actual
numbers rather than reasoning about each mechanism in isolation.

**Known by construction, never selected after the fact.** Every fixture's
verdict is declared in code *before* the fixture is run through anything
that could produce a verdict — the same discipline
`.claude/rules/agent-honesty.md` requires of the research agent itself
(pre-registered falsification, applied mechanically) and the same one
`step-10-gate-script.md` already named for `GATE5PROBE`. A golden set
whose "known" answers were determined by running candidates and keeping
the ones that happened to confirm or reject would be exactly the kind of
after-the-fact favorable selection this project's rigor rules forbid
everywhere else, applied to the one place — a golden set — where it would
be hardest to notice because there is no external hypothesis being
tested, only the harness's own fixtures.

---

## 5. Verification

Two independent, sequential layers, deliberately not one:

**Layer 1 — numeric correctness, no database, no LLM.** Every fixture's
rule and series were passed directly to `run_backtest()` and
`test_significance()` in an in-memory OHLCV `DataFrame`, and the real
`sharpe_ratio`, `num_trades`, and `p_value` were checked against what each
case's expected status requires, with real, stated margin. This is what
produced Section 2's table and surfaced the grounding-tier problem and the
unexplained trade count.

**Layer 2 — database persistence.** All six fixtures were built against
the real dev database (`strategy_research`, the same target
`verify_stage5_gate.py` uses, for the same reason: a real MCP subprocess
resolves its own database connection independently, so there is no
practical way to redirect it at a test database) via `eval.fixtures` and
`eval.golden_cases` directly, then torn down with `cleanup` and confirmed
clean with `verify_cleanup`'s direct-query check. This layer is
structurally incapable of being satisfied by Layer 1 alone — Layer 1's
in-memory `DataFrame` never touches Postgres — and it is precisely what
caught the ticker-length bug, which had nothing to do with whether the
numbers were correct and everything to do with whether the fixture could
be persisted and cleanly removed.

**What this does not prove.** Neither layer runs a fixture through the
real execution loop (`agentic_core.loop_graph.build_graph`) or through
`agentic_core.verdict.render_verdict`. That means this component has
verified that the *evidence* each fixture would present is correct, and
that the *fixture itself* persists and tears down cleanly — but it has
not yet verified that a real, Bedrock-driven agent, choosing its own tool
calls against this exact data, actually reaches the declared
`expected_status`. `GATE5PROBE`'s own live-loop proof in Stage 5 is
evidence this mechanism works *in general*; it is not evidence for these
six *specific* fixtures. That is deliberately Component 2's job, not
this one's — running each `GoldenCase` through the real loop and real
`render_verdict`, and scoring the actual result against what this
component declares it should be.

---

## 6. Interview defense

**"Walk me through why three different planted-false mechanisms, rather
than three cases that just look bad."** `decide_status` has three
independent gates — pre-registered falsification, the mandatory control,
and sample-adequacy — and a regression in any one is a realistically
different bug from a regression in another (a loosened threshold vs. a
flipped comparison vs. a dropped check entirely). Three cases that all
happened to fail through the same gate would give triple coverage of one
failure mode and none of the other two. Building one case per gate — a
random walk with no edge at all, a rule whose entire profit is an
uninformative-entry illusion the anchored control catches, and an
outright inverted rule that breaches the bar directly — means a
regression anywhere in that function has a minimal, specific case
targeting it.

**"Why didn't you just reuse `GATE5PROBE`'s own fixture for your first
confirm case, instead of building a near-identical parallel one?"**
`golden_true_1` *is* deliberately built from the same proven v3 shape —
reusing hard-won knowledge about settle-bar timing rather than
re-discovering it. But it uses a different ticker, different seeds, and
lives in this project's own Stage 6 module rather than importing from
`verify_stage5_gate.py`, because that script's gate already passed live
and I did not want touching it — even a pure import — to put its
already-earned evidence back in question. `golden_true_2` then goes
further: genuinely different dip/rally magnitudes, noise level, and
signal count, specifically because `stage-5-summary.md` itself named
"one real confirmed hypothesis, and it's synthetic" as a real weakness,
and a second confirm fixture that's just a re-seed of the first wouldn't
meaningfully answer that criticism.

**Hard question: "Giving `golden_caveat_thin_sample` a different, weaker
grounding tier than every other case — isn't that moving the goalposts
until the test passes?"** I want to be precise about what happened rather
than defend it reflexively. The tier was changed *because* the original
choice (`"none"`, matching every other case) produced a result that broke
the case's purpose — not because I was searching for whatever tier made
the test go green. The mechanism is disclosed in this exact document:
computing the real corrected threshold at `tier="none"` showed this
fixture's p-value doesn't clear it, which means it would fail
`mandatory_control` too and collapse into a second rejected case instead
of an inconclusive one. Given that, using a case-appropriate tier is the
correct fix — a thin, ambiguous sample and a confidently wrong result are
different epistemic situations, and treating the caveat case differently
is defensible on those terms alone. But I'd rather state plainly that the
number came first and the argument came after, than let the argument's
soundness imply I designed it that way from the start.

**"You found a real, unexplained anomaly — the trade count on
`golden_false_breaches_bar` — and chose not to chase it down. Doesn't
this project's whole ethos say to trace exactly that kind of thing?"**
Generally yes — Component 8's own v1/v2 fixture failures were fully
root-caused before shipping, and that discipline is real and valuable.
The difference here is what the anomaly threatens: those earlier failures
meant the fixture's *verdict itself* was wrong (v1 failed the control
entirely; v2 produced negative Sharpe on what should have been a winning
pattern) — tracing them was necessary because the fixture didn't work
without it. Here, the fixture already produces the correct verdict by an
overwhelming margin regardless of the explanation; the open question is
cosmetic (why 26 instead of ~59), not load-bearing. Spending more time on
it now would trade progress on the actual harness — the piece Stage 6 is
actually for — for closing a curiosity that doesn't change what this
component needs to prove. I'd revisit it if a future case ever depended
on trade count matching a tighter estimate, which none currently do.

---

## 7. What comes next and why

**Component 2 — `src/eval/harness.py`.** Takes each `GoldenCase`, drives
it through the real execution loop (`build_graph`, `initial_state` — real
Bedrock choosing every tool call, exactly as `GATE5PROBE`'s own live proof
did) and the real `render_verdict`, then scores three things per
`docs/architecture.md` Section 9: does the actual status match
`expected_status`; did the verdict validate cleanly (or did
`VerdictValidationError` fire, itself a meaningful finding for a case
built on unambiguous evidence); and, for `golden_caveat_thin_sample`,
does `expected_caveat_substring` actually appear in the rendered caveats.

**If this component were wrong** — if one of these six fixtures' declared
`expected_status` turned out not to be what the real evidence actually
supports — the failure would not surface here. Section 5 already states
this plainly: nothing in this component runs a fixture through the real
loop or verdict. It would surface as either a golden-set case that never
passes even against a correctly-functioning agent (a false alarm, wasting
investigation time on every future run) or, worse, a case whose
"expected" status is subtly wrong in a way that makes a real agent
regression look like a pass. That is exactly why Layer 1's real,
pre-verified numbers (Section 2's table) exist before any of this is
trusted, and exactly why Component 2's own job — actually running these
fixtures through the live system and confirming the real verdict matches
what this document declares — is necessary and not redundant with the
verification already done here.
