# Step 10 — Component 8, the Stage 5 Gate Script

## 1. What this does

`scripts/verify_stage5_gate.py` is Stage 5's actual verification gate —
Sacred Gate 2, "prove the agent never fabricates, AND that it kills
hypotheses when the evidence says to." It runs and it **passed**, 6/6
checks, live, against real infrastructure. This is the last component of
Stage 5.

Component 7 already proved two-thirds of Gate 2 on real data: the
fabrication-prevention mechanism (mutation-tested, and demonstrated on a
real rejected verdict) and the kill path (a real hypothesis, correctly
rejected, on a study that ended with a deceptively decent-looking final
Sharpe of 0.94). Two things were still unproven when this component
started. First, the **confirm** path — never once run against real data;
every real study this project has ever executed came back rejected, and an
agent that rejected everything would have passed every one of Component
7's own tests. Second, an **adversarial fabrication attempt against the
live system** — Component 7's fabrication tests all hand-built a bad
`Claim` object and fed it directly to `validate_claims()`, which proves the
checker works but never proves a real Bedrock response containing a
fabricated number is actually caught when it reaches the live pipeline.

This component closes both, using only Stage 5's own tooling — no
dependency on Stage 6's golden set, which the previous step's own text
originally implied and which was a real, corrected mistake (see
`docs/explanations/stage-5/commit-log.md`'s "Stage 5 gating decision"
entry for the full reasoning, and section 3 below for why that dependency
would have been circular).

**What this is not:** it is not a claim that this rigged fixture resembles
a real trading strategy, or that Component 7's mechanism will correctly
judge every real hypothesis it is ever handed. It is a claim that the
mechanism *can* reach `confirmed` on genuinely strong, honest evidence, and
*cannot* be made to persist a fabricated number — both proven once, live,
with real margin. Section 5 states exactly what this does and does not
establish.

New: `scripts/verify_stage5_gate.py` (the whole script).

---

## 2. Every meaningful line explained

### The fixture — three attempts, told in order because the first two failures are as instructive as the third success

The module docstring on `build_probe_series` records all three designs
because the two failures are real findings about how this project's own
backtester behaves, not throat-clearing before the "real" content.

**v1 — a perfectly periodic staircase**, alternating between two fixed
price levels (100 and 130) with an entry rule that buys whenever price is
at the low level. Run through the real `run_backtest` and
`test_significance`: 70 trades, 100% win rate, Sharpe 0.24 — and
**p-value 1.0**. Complete control-gate failure, despite what looks like an
unambiguous winner.

The reason is exactly what the mandatory control exists to catch. A fully
deterministic, perfectly repeating price series gives **randomized**
entries at the same trade frequency an equal shot at every jump — there is
nothing informative about timing when every transition in the whole series
is identical. The "rigged" rule wasn't actually smarter than a coin flip on
this specific data; it just happened to also win, the same way anything
trading this instrument at this frequency would. This is a real,
substantive validation of the control gate's purpose, encountered by
accident while trying to build a fixture that should obviously pass.

**v2 — an engineered dip immediately followed by a rally the very next
bar.** Sharpe went **negative** (-1.8 and -1.27 across two independent
windows), win rate collapsed to ~33%. The cause: `backtesting.py` fills
orders at the **next bar's open**, not the signal bar's close — this
project's own Stage 2 no-lookahead discipline, working exactly as
designed, in a place its author hadn't accounted for. The entry signal
fired correctly on the dip bar, but the resulting order filled one bar
later — and in v2's design, that following bar was already the
post-rally price, because the rally happened immediately with no bar in
between to hold the dip level steady. The strategy was systematically
buying *after* the rally, near the top, and losing money on the reversion
back to noise.

This is worth restating plainly: the engine's own correctness (never
executing on stale information) actively broke a fixture that assumed
same-bar execution. That is the sacred-gate discipline paying for itself
in an unplanned place.

**v3 — the version used** — inserts a one-bar *hold* after every
engineered price move: dip, hold-low, rally, hold-high. Any order queued
off a given price level now fills at a stable, *unchanged* open on the
following bar, regardless of the next-bar-open timing model. The entry
condition (`close < 0.93 × close[offset=-1]`, i.e. today dropped more than
7% versus yesterday) and exit condition (`close > 1.10 × close[offset=-1]`,
today rose more than 10%) are both far outside the ordinary
random-walk noise (0.4% daily standard deviation) injected between
cycles, so they fire only on the engineered events, never on ordinary
fluctuation.

```python
entry=_leaf(PriceTerm(field="close"), "lt",
            ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=0.93)),
exit=_leaf(PriceTerm(field="close"), "gt",
           ScaledTerm(term=PriceTerm(field="close", offset=-1), factor=1.10)),
```

`ScaledTerm` wrapping a `PriceTerm(offset=-1)` is what makes this a
*relative* comparison against yesterday's close rather than a fixed
absolute level — necessary because, unlike v1's series, v3's price levels
drift over time as noise compounds between cycles, so no single constant
threshold would reliably separate "ordinary noise" from "engineered
event" across the whole series.

### Results, on the actual functions, before any LLM was involved

Both windows, independently seeded (`seed=1` for in-sample, `seed=2` for
out-of-sample — different noise realizations, not the same series twice):

| Metric | In-sample | Out-of-sample |
|---|---|---|
| Sharpe ratio | 0.932 | 0.932 |
| Trades | 61 | 61 |
| Win rate | 100% | 100% |
| p-value (`n_resamples=999`) | 0.001 | 0.001 |

The p-value needs one honest caveat, and it changed the design mid-probe.
At `n_resamples=999`, 0.001 is the **resample floor** — literally zero of
999 randomized configurations matched or beat the observed Sharpe. The
first probe ran at `n_resamples=300`, whose floor is `1/301 ≈ 0.00332` —
also floored, but uncomfortably close in absolute terms to the strictest
grounding tier's `0.005` threshold, purely as an artifact of resample
count rather than a weak result. Raising `n_resamples` to 999 (free, local
compute, no API cost) removed that ambiguity and gave real margin: 5x
under the strictest tier rather than 1.5x.

### Seeding real cached data for a synthetic ticker

```python
def seed_price_bars(session, ticker: str, series: pd.DataFrame) -> None:
    ...
    session.add(PriceBar(
        ticker=ticker, date=row["date"],
        raw_open=price, raw_high=price, raw_low=price, raw_close=price, raw_volume=1_000_000,
        adj_open=price, adj_high=price, adj_low=price, adj_close=price, adj_volume=1_000_000,
        ...
```

The gate script inserts a synthetic `GATE5PROBE` ticker directly into the
**real dev database** (`strategy_research`), not the test database. This
is a deliberate, disclosed departure from how the automated pytest suite
operates, and it isn't optional: the execution loop's tools run inside a
real MCP server launched as a **separate OS subprocess**
(`python -m mcp_tools.server`), and that subprocess resolves its own
`SessionFactory` from `settings.database_url` independently of anything
this script's own process does. There is no practical way to monkeypatch a
separately-launched subprocess's database connection from the parent
process. Stage 4's own gate script and Component 6b's `run_study.py`
already established this same constraint and the same answer: run against
the real database, then clean up completely, and verify the cleanup by
direct query rather than trusting the script's own accounting.

### Building the charter, hypothesis, and design directly, not through Components 2/4/5's own LLM calls

```python
charter = Charter(parsed=ParsedCharter(...), resolved_universe=[TICKER], ...)
```

The charter and hypothesis objects are constructed directly in Python, not
produced by `charter.parse_charter` or `hypothesis.propose_hypothesis`.
This is deliberate: those functions exist to translate a human's fuzzy
English or a literature query into structure, and neither translation step
is what this gate needs to test. Going through them would add two more
real LLM calls and two more points of non-determinism to a script whose
entire purpose is an unambiguous, repeatable proof — and Components 2 and
4 already have their own live verification on record. `grounding_tier` is
set to `"none"` deliberately, the strictest multiple-comparisons tier
(§`verdict.py`'s `TIER_SEARCH_BURDEN`), so a pass here proves the confirm
path survives even the harshest correction the system can apply, not just
the easiest one.

### The disclosed gap between windows

```python
# A real StudyDesign from Component 5 never has a gap between windows --
# _simple_holdout slices one continuous trading-date list. This one does
```

`in_sample` and `out_of_sample` here are two **independently generated**
series with a real calendar gap between them (in-sample ends
2022-11-01, out-of-sample begins 2023-01-02), rather than one continuous
history sliced in two the way `propose_study_design` always produces.
`StudyDesign`'s own validator only requires `out_of_sample.start >
in_sample.end`, which this satisfies, so it's schema-legal — but it is not
the shape a real design ever has, and the code says so explicitly rather
than leaving a future reader to assume this script exercises
`propose_study_design`'s own logic. It doesn't; that's Component 5's gate,
already passed.

### Running the real loop and the real verdict

```python
graph = build_graph(lambda: session, structured_output, design_id=design_id, hypothesis_id=hyp_id)
final = await graph.ainvoke(initial_state(charter, hypothesis, design))
...
verdict_id, verdict = render_verdict(study_run_id)
```

`structured_output` — the real `llm_client` function, real Bedrock — is
passed directly as the loop's `llm` argument, exactly the way
`scripts/run_study.py` does it for a genuine study. Nothing about the
graph, the gating, or the retry logic differs from a real run; only the
underlying evidence is synthetic. This is what makes the result meaningful
as a proof of the *system*, not just of the fixture: the agent had to
choose to call `run_backtest`, then `test_significance`, then decide to
advance or conclude, using the same dynamically-built, tier-gated action
schema every real study uses.

### `ClaimCorruptingLLM` — the adversarial injection

```python
class ClaimCorruptingLLM:
    def __call__(self, prompt, response_model):
        self.calls += 1
        result = structured_output(prompt, response_model=response_model)
        if response_model is ParsedVerdict and result.claims:
            corrupted = result.claims[0].model_copy(update={"value": result.claims[0].value + 999.0})
            result = result.model_copy(update={"claims": [corrupted, *result.claims[1:]]})
        return result
```

This calls the **real** `structured_output` first, gets a **real** Bedrock
response, and only then corrupts one claim's value before returning it —
injecting the fabrication at the exact point `render_verdict` would trust
the model's output, on every attempt including retries (since it wraps
every call, not just the first). This is the direct analogue of Stage 2's
own gate discipline: deliberately attempt the violation and confirm the
system refuses it, rather than waiting to observe a real model
spontaneously lying, which cannot be reliably provoked or scheduled.
`+999.0` is chosen simply to guarantee the corrupted value cannot
accidentally land within `validate_claims`'s tolerance band of the real
traced value.

### The test bug this component found in itself, and how it was actually diagnosed

The first run of Job 2 produced:

```
[PASS] corrupted claim was rejected by validation
  -- VerdictValidationError raised after retries:
     ['claim 0 states sharpe_ratio=999.93..., but trace 21 recorded 0.93...']
[FAIL] no verdict row was ever written for the fabricated attempt
  -- 1 verdict rows found
```

The natural first reaction to a failing assertion is to suspect the code
under test. The right move here was the opposite: read what the *passing*
check right above it had already proven. The error message names the
exact corrupted value against the exact real traced value — that is
direct evidence the detection worked correctly. Given `render_verdict`
only reaches its row-write branch on the success path (`if parsed is
None: raise ...`), and this call raised, it could not have written a row.
So the "1 verdict rows found" could not be Job 2's fabricated row — it had
to be Job 1's own **legitimate** confirmed verdict, written moments
earlier against the same `study_run_id`, which Job 2 deliberately reused
to avoid the cost of a second live loop run.

The bug was in the assertion, not the system: it checked for `count == 0`
when the honest expectation, given a legitimate verdict already existed,
was `count == 1` (unchanged). Fixed by capturing the count before Job 2
runs and asserting it is unchanged afterward:

```python
before = session.query(VerdictRow).filter(VerdictRow.study_run_id == study_run_id).count()
...
after = session.query(VerdictRow).filter(VerdictRow.study_run_id == study_run_id).count()
record("the fabrication attempt added no new verdict row", after == before, ...)
```

Re-run: `verdict count before=1, after=1` — pass. This is recorded as a
genuine finding, not smoothed over, because it demonstrates the exact
discipline `docs/architecture.md` names as the hard part of this whole
project: telling "the system did something wrong" apart from "my test
asked the wrong question" requires actually tracing the causal chain, not
pattern-matching on a red result.

### A secondary finding, deliberately not fixed here

Diagnosing the bug above surfaced a real, separate gap: `render_verdict`
has no guard preventing it from being called twice against the same
`study_run_id`. Nothing in this project's normal flow ever does that — a
study gets verdicted once — but nothing in the code prevents it either,
and there is no unique constraint on `verdicts.study_run_id`. This
component's own test exposed the gap only because it deliberately called
`render_verdict` twice to avoid a second live loop run. Logged here as a
Component 7 follow-up rather than patched mid-gate: fixing it is not what
Sacred Gate 2 requires, and a gate script's job is to prove the gate, not
to accumulate unrelated hardening along the way.

### `cleanup` and `verify_cleanup`

Every row this script's own execution creates — price bars, charter,
hypothesis, study design, study run, tool call traces, verdict — is
deleted in a `finally` block, so cleanup runs even if an assertion above
it fails. `verify_cleanup` then re-queries the database directly rather
than trusting that the deletion calls succeeded, matching the standing
practice this project has used since Component 5: verify state by
querying it, never by assuming a written line of code did what it says.

### What was skipped

Genuine boilerplate: `record()` (a three-line accumulator matching
`verify_stage3_gate.py`/`verify_stage4_gate.py`'s own helper exactly), the
final pass/fail summary printer, and the `StdioServerParameters` /
`stdio_client` launch, which is identical to `scripts/run_study.py`'s own
and already fully explained in
[step-08-live-execution-loop.md](step-08-live-execution-loop.md).

---

## 3. Design decisions and rejected alternatives

### A synthetic, constructed fixture — never a real, selected hypothesis

**Chosen:** the confirm-path proof uses a hand-built rule and hand-built
price data, both engineered before any test ran, with the answer known in
advance by construction.

**Alternative considered:** run several real hypotheses (via Components 2
and 4's normal pipeline) against real market data until one happens to
confirm, and use that as the proof.

**Why rejected, explicitly, at the user's direction:** selecting a real
hypothesis *because* it happened to pass would be exactly the kind of
after-the-fact favorable selection `.claude/rules/data-pipeline.md` and
`.claude/rules/backtesting-rigor.md` already exist to forbid everywhere
else in this project — screening thresholds must be relative and
disclosed, universe selection must be point-in-time, and a strategy's
literature source must predate seeing its own results. There is no
principled reason a gate script should be exempt from a discipline this
strict everywhere else. A synthetic fixture with a known, engineered,
statistically overwhelming edge is the same choice Stage 2's own gate made
for lookahead: build the thing that is *guaranteed* to reveal the
behavior, rather than search for a naturally-occurring instance of it.

**Cost to reverse:** none intended — this is the correct design, not a
placeholder.

### Real Bedrock for both the loop's decisions and the verdict's narrative

**Chosen:** `structured_output` (real Bedrock) is used for every
`decide_next_action` call and for `render_verdict`'s narrative — the
entire live agent, not a scripted stand-in, driving itself to a confirmed
verdict.

**Alternative considered:** drive the loop with Component 6a's own
`LazyAgent` (free, deterministic) and spend real money only on the final
verdict-writing call.

**Why rejected:** that would prove the verdict mechanism can write an
honest "confirmed" narrative around numbers it's handed — a real and
useful thing to know, but a narrower claim than "the live system, making
its own real decisions at every step, correctly reaches a confirmed
verdict end to end." The whole point of a gate script, in this project's
own established style, is proving the actual thing that will run in
production, not a cheaper approximation of it.

**Cost to reverse:** would cut the real API spend roughly in half, at the
cost of a materially weaker claim. Not worth it for a one-time gate proof.

### Reusing one live study run for both jobs, not paying for two

**Chosen:** Job 2's adversarial fabrication attempt reuses Job 1's
already-completed `study_run_id` rather than running a second full live
loop.

**Alternative considered:** give Job 2 its own fresh study run, so its
verdict-count assertion could simply check for zero rather than needing
the before/after comparison the actual bug required.

**Why the reuse was kept, even after it caused the test bug:** the traces
a corrupted-claim test needs are already real, already correct, and
already sitting in the database from Job 1 — Job 2 only needs to corrupt
the *response*, not generate new evidence to corrupt. Paying for a second
full loop run (roughly six more real Bedrock calls, per Component 6b's own
observed 3.0-steps-per-window rate) to avoid a five-line assertion fix
would be optimizing for the wrong thing. The bug this reuse caused was
real and is recorded in full above, precisely because hiding it by
switching to the more expensive design would have been the easier and
less honest path.

**Cost to reverse:** trivial, and not warranted.

### `n_resamples` raised from 300 to 999 mid-probe

**Chosen:** 999, after the 300-resample floor (`0.00332`) landed
uncomfortably close to the strictest tier's `0.005` threshold in absolute
terms, despite being genuinely floored (0 of 300 null samples beat the
observed Sharpe either way).

**Why this isn't the same situation as Stage 4's own `n_resamples`
calibration:** Stage 4 traded resolution against real runtime cost for a
production tool that runs inside every study. This is a one-time,
local-compute-only gate script; there is no cost to raising resolution
here, so there was no real tradeoff to weigh — just a numerical
coincidence worth removing rather than living with.

---

## 4. Concepts introduced

**The Monte Carlo permutation null, and why a perfectly deterministic
series defeats it.** `test_significance` asks: if entries were placed
randomly, at the same frequency, would they do about as well? On a
perfectly periodic series (v1), *any* entry timing eventually rides the
same guaranteed jump, because the jump is everywhere in time with equal
regularity — there is no informational content in choosing *when* to
enter, only in choosing *whether* to trade at all. This is a sharper,
more concrete statement of what "beating randomized entries" actually
measures than an abstract description would give: it measures whether
*informed timing* adds value, not whether a strategy makes money.

**Order execution timing as a source of real, non-obvious bugs.**
`backtesting.py` fills orders at the next bar's open by default — a
one-sentence fact that is easy to state and easy to forget while designing
a fixture. v2's negative Sharpe is a concrete demonstration of the
consequence: a signal detected on bar *i* does not act on bar *i*'s price,
it acts on whatever bar *i+1* happens to open at. Any fixture, test, or
mental model that assumes same-bar execution will be systematically wrong
in a way that's easy to misdiagnose as "the strategy doesn't work" rather
than "the fixture doesn't match the execution model."

**A gate script as a genre, not just a name.** This project now has four
of them (Stages 2 through 5), and they share a shape worth naming
explicitly: each is a single, self-contained script, run manually, that
deliberately *attempts* the exact failure the stage's gate is worried
about — lookahead, literature-consistent results, real MCP mechanics,
fabrication and confirmation — rather than accumulating confidence from
unit tests that were written to describe the system's own behavior.
Attempting the failure and watching it get caught is categorically
stronger evidence than describing the failure and trusting the
description.

**Distinguishing a broken system from a broken test.** The retraction
worked through in section 2 above is the concrete instance, but the
general skill is: a failing assertion names a *disagreement* between
expectation and observation, and either side can be wrong. The discipline
is to read what the *other*, passing checks already established (here,
the error message itself proved detection worked) before concluding the
mechanism under test is at fault.

---

## 5. How Sacred Gate 2 was satisfied — and what this does not prove

### The confirm path, live, for the first time

`status == "confirmed"`, reached through the real loop and the real
verdict writer, on evidence that clears every gate by a wide margin
(Sharpe 0.93 against a 0.5 bar; p=0.001 against a 0.005 strictest
threshold; 61 trades against a 30 floor, in both windows). Six claims in
the resulting verdict, each traceable to a real `tool_call_traces` row.

This is the first time in this project's history that a hypothesis has
been mechanically confirmed on real data. Every prior real study —
Component 6b's two live runs, Component 7's own verification — came back
rejected. Without this component, the honest claim about Component 7
would have remained "provably kills, never observed to confirm," and an
agent that rejected everything would have been indistinguishable from a
correctly functioning one by any evidence this project had.

### The fabrication attempt, live, for the first time

A genuinely real Bedrock response, corrupted after the fact at the exact
trust boundary, was rejected — `VerdictValidationError`, naming the exact
fabricated value against the exact real traced value — on every retry
attempt, with zero persisted verdict added on top of the one legitimate
row already present.

### What this does not prove

**One confirm, not general reliability.** This proves the mechanism *can*
reach `confirmed` correctly on unambiguous evidence. It does not prove
Component 7 will draw the line correctly on a real, ambiguous hypothesis
— one where the honest answer is a close call rather than an engineered
landslide. That is a different, harder property, and it is exactly what
Stage 6's golden set exists to test at scale, with cases specifically
designed to be non-obvious.

**One fabrication attempt, one shape.** `ClaimCorruptingLLM` corrupts
exactly one claim's numeric value. It does not test a corrupted *metric
name*, a corrupted *trace reference*, or a fabricated number hidden in
prose with no claim at all — all of which Component 7's own unit tests
already cover synthetically, but none of which have now been proven
against a real live response the way the numeric-value case has.

**The double-verdict gap remains open.** Logged, not fixed. A future
caller that verdicts the same study run twice would get two rows, and
nothing here or in Component 7 prevents it.

**This is still not Stage 6.** It closes Stage 5's own gate using Stage
5's own tooling, on two carefully chosen cases. Stage 6's golden set,
running continuously against real hypotheses generated by the real
pipeline, is what turns "proven possible" into "proven reliable" — and it
remains a genuinely separate, later piece of work, not something this
component substitutes for.

---

## 6. Interview defense

**"Walk me through what this gate script actually proves that Component 7
alone didn't."** Component 7 proved fabrication prevention thoroughly —
mutation-tested, and demonstrated on one real rejected verdict — but it
had never once produced a `confirmed` status on real data, which meant an
agent that rejected everything would have passed every one of its own
tests. This component closes that: a deliberately constructed fixture
with a known, engineered, overwhelming statistical edge, run through the
real execution loop and the real verdict writer with real Bedrock making
every decision, correctly reaches `confirmed`. Separately, it proves
fabrication resistance against a *real* corrupted model response, not
just a synthetic bad object handed directly to the validator in a unit
test.

**"Why didn't you just pick a real hypothesis that you expected to
confirm?"** Because choosing a real case *because* it was expected to pass
would be exactly the kind of after-the-fact favorable selection this
project's own rigor rules forbid everywhere else — universe screening
must be relative and disclosed, not hand-picked after seeing what looks
good, and the same logic applies to picking a gate's own proof case. A
synthetic fixture with a provably overwhelming, engineered edge is the
honest version of the same idea Stage 2's own gate used for lookahead:
build the thing guaranteed to reveal the behavior, rather than go looking
for a naturally occurring instance and risk quietly selecting a lucky one.

**Hard question: "Your first version of this gate script had a bug and
reported a false failure. Doesn't that undermine confidence in the whole
gate?"** I'd argue the opposite, and I'd want to walk through exactly why
rather than just assert it. The failing check's own error message already
contained the proof that detection worked correctly — it named the exact
corrupted value against the exact real traced value. The bug was that my
assertion asked "is the verdict count zero" when the honest question,
given a legitimate verdict already existed from the first job, was "did
the count change." Diagnosing that required tracing render_verdict's own
control flow — confirming it can only reach its write branch on success,
and this call had raised — rather than assuming a red result meant the
system was broken. A gate whose author is willing to find and fix a bug
in the *gate itself*, and say so plainly rather than quietly rerunning
until it passes, is more trustworthy than one that happened to pass on
the first try with no scrutiny applied to why.

**"What would you do differently?"** Two honest things. First, I ran the
live script a third time, unnecessarily, purely to reformat some grep
output after I already had a clean 6/6 result — real wasted API spend
from carelessness, not from any genuine need to re-verify. I'd be more
careful separating "I want to re-read output I already have" from "I need
to re-run something that costs money." Second, I'd want the sample-size
margins on this fixture reviewed against realistic production data
volumes rather than the very generous ~740-bar windows used here, since a
real study's out-of-sample window could plausibly be much shorter — though
that's a question about realistic *studies*, not about whether this gate
script does its stated job.

---

## 7. What comes next and why

**Stage 5 formally closes.** `docs/explanations/stage-5/stage-5-summary.md`
is written next, synthesizing all eight components and this gate rather
than repeating any of their own step files.

**Stage 6 — the evaluation harness — begins as a genuinely separate stage
built on a closed Stage 5,** not as the mechanism that closed it. Its
golden set (planted-true, planted-false, and known-caveat hypotheses) is
where the confirm path gets tested at scale and on genuinely ambiguous
cases, which this component's one engineered example cannot do by design.

**If this component were wrong** — if the fixture's edge were somehow an
artifact rather than real, or if the corruption test had a hole — the
failure would surface exactly the way Stage 6 is built to catch it:
planted-false hypotheses starting to pass, or fabricated claims starting
to slip through, in continuous production monitoring rather than in a
one-time manual check. That is precisely why this gate being narrow and
honest about its limits, rather than overstated, is what makes Stage 6
meaningful work rather than redundant confirmation of something already
fully proven.
