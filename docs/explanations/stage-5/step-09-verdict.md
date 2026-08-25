# Stage 5, Component 7: the verdict

## 1. What this component does

This is where the study becomes an answer. Component 6's loop wrote a trail
of tool calls into `tool_call_traces`; Component 7 reads that trail, decides
whether the hypothesis lived or died **in deterministic code**, has the
language model write prose around numbers that are already locked to those
traces, validates every claim, and writes one `Verdict` row. It is the first
component permitted to set a hypothesis's final status.

It is also where **Sacred Gate 2** is satisfied — the project's second
non-negotiable verification requirement: *prove the agent never fabricates,
and that it kills hypotheses when the evidence says to.* Two claims, two
different mechanisms, and the second is the harder one.

**What exists now that did not before:** three deterministic gates that read
real numbers out of the database; a claim validator that rejects any
quantitative statement not backed by a recorded tool call; a scan that
catches invented numbers hiding in prose where no claim references them; a
multiple-comparisons correction that treats grounding as an assumed search
burden; code-generated mandatory caveats a model cannot drop; and a real,
stored verdict on a real study that opens with the words *"The hypothesis is
dead."*

**Scope boundaries.** Component 7 does not maintain the scoreboard, does not
schedule decay re-testing, and does not re-verdict on new data — those are
architecture.md's Steps 6 and 8. It also deliberately refuses to write a
verdict for a study run whose status is not `completed`: a failed run has
untested windows, and a verdict drawn from partial evidence is precisely the
dishonest output this component exists to prevent.

New: `src/agentic_core/verdict.py`, `scripts/render_verdict.py`,
`tests/agentic_core/test_verdict.py` (25 tests), migration `aaa61daf9d89`.
Modified: `src/agentic_core/schemas.py` (three new models),
`src/agentic_core/db/models.py` (two new columns).

---

## 2. Every meaningful line explained

### The two schema splits, and the field that is absent

```python
class Claim(BaseModel):
    statement: str
    tool_call_trace_id: int
    metric: str
    value: float
```

`metric` is the field that makes validation exact, and it was not in the
shape the `verdicts.claims` column comment originally sketched. Without it,
checking `value` would mean matching against *any* numeric field in the
referenced trace — so a fabricated claim about `sharpe_ratio` could validate
against a `p_value` in the same trace that happened to be numerically close.
A test exercises exactly that: a claim asserting `p_value=0.771` against a
trace whose `sharpe_ratio` is 0.771 must be rejected, and it is.

```python
class ParsedVerdict(BaseModel):
    narrative: str
    claims: list[Claim]
    caveats: list[str] = Field(default_factory=list)
```

Note what is **absent**: `status`. The outcome is decided by
`decide_status`, and the model is told that outcome before it writes a word.
This is the same structural move as `StudyDesign` having no
`control_required` field ([step-06](step-06-study-design.md)) and `CallTool`
having no date field ([step-07](step-07-execution-loop-state.md)): there is
no field here the model could fill in that changes the result. It is
explaining a decision, never making one.

### `corrected_threshold` — and why the existing BH tool is not used

```python
def corrected_threshold(hypothesis_count: int, grounding_tier: str) -> float:
    burden = TIER_SEARCH_BURDEN[grounding_tier]
    effective_tests = max(1, hypothesis_count) * burden
    return BASE_ALPHA / effective_tests
```

Stage 4 already built `research_stats.multiple_comparisons.correct_p_values`,
which implements Benjamini-Hochberg — and Component 6a deliberately excluded
it from the agent's tool vocabulary specifically so it could be applied here,
at verdict time, by code rather than by a model typing p-values by hand. So
it is worth being explicit about why this function does not call it.

**Benjamini-Hochberg corrects a *list* of p-values simultaneously; it needs
the full set.** But verdicts render sequentially, one per completed study,
and a hypothesis that has not been tested yet has no p-value to include.
There is no way to BH-correct hypothesis 3 of an eventual 40 at the moment
hypothesis 3 finishes. The method fits the problem in principle and is
inapplicable at this point in time.

What happens instead is a Bonferroni-style threshold on the count so far:
divide the base alpha by the number of tests. It is sequential-safe and
strictly conservative. The consequence worth naming is that an early verdict
used a laxer bar than a later one will — hypothesis 1 faced `0.05/1` while
hypothesis 40 faces `0.05/40`. That asymmetry is real, and the fix is not to
paper over it here but to preserve the raw p-values and the count in the
verdict row so a proper cross-charter BH re-evaluation can run later. That
re-evaluation can **demote** an earlier confirmation as evidence
accumulates, which is exactly the "previously believed, now decayed" concept
architecture.md Step 6 already describes — so it belongs to the scoreboard,
not here.

`max(1, hypothesis_count)` guards against a zero count producing a division
by zero; it cannot legitimately be zero (the hypothesis being verdicted is
itself counted), but a defensive floor costs nothing and a `ZeroDivisionError`
inside a verdict would be an ugly way to discover a query bug.

### `evaluate_windows` and `_metric_from`

```python
def _metric_from(sources: list[dict | None], metric: str) -> float | None:
    for src in sources:
        if src and metric in src and src[metric] is not None:
            return float(src[metric])
    return None
```

A falsification metric can live in either trace. `sharpe_ratio`,
`annual_return_pct`, `max_drawdown_pct` and `num_trades` come from
`run_backtest`; `p_value` comes from `test_significance`. The
`FalsificationCondition` vocabulary defined back in Component 4 spans both
(see `schemas.py`), so a function that looked in only the backtest trace
would silently return `None` for a perfectly valid `p_value` condition — and
the gate would report the hypothesis as unevaluable rather than evaluating
it. Searching both sources in order removes an entire category of
false-inconclusive.

```python
is_out_of_sample=w > 0,
```

Window 0 is the in-sample window in **both** design types, because
`flatten_windows` puts it first for a `simple_holdout` and
`walk_forward_windows[0]` *is* `in_sample` by construction (see
[step-06](step-06-study-design.md)). That uniformity is why this line can be
a simple index comparison rather than a design-type branch.

```python
backtest_trace_id=bt.id if bt else None,
significance_trace_id=sig.id if sig else None,
```

These are carried through into the prompt so the model is *handed* the exact
trace id each number needs. This matters more than it looks: a model forced
to guess a trace id will guess wrong, its claims will fail validation, and
the verdict will be rejected and retried — burning money to produce a
failure that better prompt construction avoids entirely.

### `decide_status` — the load-bearing function

```python
oos = [e for e in evaluations if e.is_out_of_sample]
```

**This single line is the most consequential in the component.** Section 3
treats it as a design decision in full, and section 5 shows what happens on
real data when it changes. Everything below reads `oos`, so narrowing it
narrows all three gates at once.

```python
unevaluable = [e.window_index for e in oos if e.metric_value is None]
if not oos or unevaluable:
    return "inconclusive", [GateResult(name="evidence_present", ...)]
```

A study with no out-of-sample evidence, or with a window missing the metric
the condition names, is `inconclusive` — never `confirmed` and never
`rejected`. Both alternatives would be wrong in a specific way: defaulting to
`confirmed` would let a broken study pass, and defaulting to `rejected` would
kill a hypothesis for a plumbing failure rather than for evidence, which is
exactly as dishonest in the other direction.

```python
falsified = [e.window_index for e in oos if _fails_bar(e.metric_value, condition)]
```

The pre-registered condition, applied mechanically. The hypothesis's own bar
— written by the agent *before any testing*, in Component 4 — read by code
that has no opinion about it.

```python
no_control = [e.window_index for e in oos if e.p_value is None or e.p_value >= threshold]
```

`e.p_value is None` is treated as failure, not as "skip this window." A
missing control is not a passed control. Without that clause a study where
`test_significance` never ran would sail through the mandatory-control gate,
which would quietly undo the guarantee Component 6a spent an entire
component enforcing.

```python
if not gate_falsification.passed or not gate_control.passed:
    return "rejected", gates
if not gate_sample.passed:
    return "inconclusive", gates
return "confirmed", gates
```

**The ordering is the asymmetry**, and it is deliberate. Failure gates are
checked first, so thin evidence can downgrade a would-be *confirmation* to
`inconclusive` but can never rescue a hypothesis the evidence already killed.
Reverse those two blocks and a window that both fails the bar and has four
trades becomes "insufficient evidence" instead of "rejected" — turning small
samples into a universal escape hatch from bad results. A mutation test does
exactly that reversal and three tests catch it.

The principle in one line: **you can always reject on evidence; you can only
confirm with enough of it.**

### `validate_claims` — Gate 2's fabrication half

```python
by_id = {t.id: t for t in traces}
```

`traces` is scoped to this study run by the caller's query. That scoping is
what makes the cross-study check work: a claim citing a real trace id
belonging to a *different* run resolves to nothing here and is rejected.
Without it, a model could support a claim with another study's evidence and
the reference would look perfectly valid on inspection. A test asserts the
same claim validates against its own run's traces and fails against this
one's.

```python
if trace.is_error:
    errors.append(f"claim {i} references trace {trace.id}, which recorded an error")
```

A tool call that errored produced no evidence. Citing it is not a smaller
problem than citing nothing — it is citing a record of failure as though it
were a result.

```python
if not _close(claim.value, float(actual)):
```

`_close` allows the larger of a 1% relative and a 0.01 absolute tolerance.
That range is chosen so a model rounding `0.6519` to `0.65` still validates —
which real models do constantly, and rejecting it would make the component
unusable — while a genuinely different number does not. A test pins both
directions: `0.65` against `0.6519` passes, `0.85` fails.

### `scan_for_unreferenced_numbers` — closing the hole validation leaves

```python
for token in _NUMBER_RE.findall(narrative):
    value = float(token)
    if any(_close(value, c) for c in claimed):
        continue
    if any(_close(value, a) for a in allowed):
        continue
    orphans.append(token)
```

`validate_claims` proves every claim is honest. It says nothing about prose.
A model could keep the claims list scrupulously clean and still write *"the
strategy returned 42.7% annually"* into the narrative, where no claim
references it — every claim validates, and the invented figure rides along
unchecked. This scan requires every numeric token in the narrative to resolve
to either a validated claim's value or a structural value from the caller's
allowlist.

It operates on the model's narrative **only**, before mandatory caveats are
appended. Those caveats are code-generated and contain code-chosen numbers;
scanning them would be this module checking its own output against itself.

### `_structural_allowlist` — deliberately tight, and initially too tight

```python
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
```

Each entry is a specific known constant, not a category. A broad allowlist —
"any integer under 100", say — would let a fabricated trade count pass as
structural, which is the exact hole this scan exists to close.

The last two entries of the first set were **missing in the first version,
and the first live run caught it**. Section 5 tells that story; the short
version is that the model correctly wrote *"requires all out-of-sample Sharpe
ratios to be at least 0.5"* and *"far fewer than the required 30 trades"*,
and both true, system-supplied numbers were flagged as unreferenced. That was
a too-tight check rejecting honest prose, not a model fabricating.

### `mandatory_caveats` — generated, never requested

```python
caveats = [
    f"This is hypothesis {hypothesis_count} tested under this charter. ...",
    "Universe membership is current, not point-in-time: ...",
]
```

architecture.md Step 9 scores "whether required caveats appeared," which only
means something if they cannot be omitted. Asking a model politely for
caveats produces them most of the time — and the times it skips one will
correlate with the times the caveat was inconvenient, which is precisely when
it mattered. Generating them in code removes the opportunity.

The first caveat discloses the hypothesis count, the corrected threshold, the
search-burden factor used, **and that the factor is a provisional assumption
rather than a calibrated measurement.** That last clause converts an
uncalibrated number from a hidden assumption into a stated one, which is the
same move `.claude/rules/data-pipeline.md` requires for survivorship bias:
measure the gap and disclose it.

The second caveat is the survivorship disclosure itself, inherited from
Stage 1's documented limitation — today's universe, not point-in-time, so
delisted and bankrupt names are absent and results are biased upward.

### `_verdict_prompt` — outcome first, deliberately

```
VERDICT: {status.upper()}
...
Write:
- narrative: a plain, honest explanation of why the verdict is {status}. Do not soften
  a rejection or hedge toward optimism. If the hypothesis is dead, say so directly.
```

The ordering is the design. A prompt that presented the evidence and then
asked *"what do you conclude?"* would be inviting exactly the judgment call
`.claude/rules/agent-honesty.md` says must never be the LLM's — and an
agreeable model looking at a `+0.941` final fold has a friendly story
readily available. Stating the decision up front makes the task
unambiguously expository.

The instruction that a number without a matching claim "will cause this
verdict to be REJECTED and rewritten" is not the enforcement — the scan is.
But telling the model the rule up front reduces how often the expensive retry
path fires.

### `render_verdict` — the orchestration

The status check is worth reading closely:

```python
if run.status != "completed":
    raise ValueError(f"... a verdict is only written for a completed run -- a failed run
                       has untested windows ...")
```

A `failed` run is one where the budget ran out or the model could not produce
a valid decision. Either way, windows went untested. Refusing to verdict it is
the same principle as Component 6's budget exhaustion routing to `failed`
rather than `conclude`: partial evidence does not get to wear the label of a
completed study.

```python
if parsed is None:
    raise VerdictValidationError(..., errors=errors, narrative=last_narrative)
```

**No verdict row is written when validation fails.** A verdict whose claims
do not resolve is not a weaker verdict — it is an unsupported one, and
storing it would defeat the only mechanism that makes fabrication checkable.

The `errors` and `narrative` payload on the exception was **added
mid-debugging**, and section 5 explains why it turned out to be the thing
that made the first live failure diagnosable at all.

### The two new columns

`verdicts.caveats` (JSONB) exists rather than folding caveats into
`narrative` because Stage 9 scores whether required caveats appeared — a
check that is trivial against a list and brittle against prose.
`study_runs.failure_reason` (Text) was deferred out of Component 6 pending
this component's design pass and decided here: a `status='failed'` row with
no reason forces whoever finds it to reconstruct the cause from traces, and
the two failure modes (exhausted retries versus exhausted budget) call for
genuinely different responses.

The migration needed the same hand-edit as `ac225385b472` before it:
autogenerate emitted `caveats` as `NOT NULL` with no default, which succeeds
only because `verdicts` happened to be empty and fails outright against any
populated database. `server_default='[]'` was added.

### What was skipped

Genuine boilerplate: imports, `_fails_bar` (a two-branch comparison),
`_close` (one arithmetic expression), and `scripts/render_verdict.py`'s print
formatting. The script's one non-obvious property is covered in section 3.

---

## 3. Design decisions and rejected alternatives

### Gate scope: every out-of-sample window

**Chosen:** both the falsification gate and the control gate read every
out-of-sample window. Any window failing fails the gate. In-sample is
reported but never decisive.

**Alternatives genuinely considered.** *Final window only* — score the study
on where it ended up, on the reasoning that the most recent period is the
most relevant to whether the edge still works. *Aggregate* — take the mean
metric across out-of-sample windows and test that single number.

**Why final-window-only was rejected:** it scores a walk-forward study on its
ending and discards the entire reason the design exists. A walk-forward
design was chosen (in Component 5) precisely because the hypothesis made a
persistence claim; checking only the last fold answers "did it work
recently," which is a different question from "does it hold up across
periods." On this project's real study it changes the falsification gate's
answer outright — see section 5.

**Why aggregate was rejected:** a mean hides instability, and instability is
exactly what a `-1.510` fold sitting between two positive ones *is*. On the
real data the mean out-of-sample Sharpe is about `-0.008`, which does fail
the bar — so it reaches the right answer here, but for the wrong reason and
while reporting one mediocre number instead of "one fold was catastrophic."
A test records this explicitly, so that the choice of per-window over
aggregate is visible as deliberate rather than as an accident of which
happened to work.

**Why in-sample is not decisive:** window 0 is the period the hypothesis was
formed against. Even though this system does no parameter fitting, it is
still the weakest evidence in the study, and letting it drive the outcome in
either direction over-weights it. A test constructs a study with a
catastrophic in-sample window and strong, deep out-of-sample windows and
asserts the verdict is `confirmed`.

**Cost to reverse:** one line, and that is exactly the problem. This is the
most load-bearing decision in the component and the easiest to "simplify"
without noticing. Three tests exist to make that impossible to do quietly.

### The sample-adequacy asymmetry, and the one number with no anchor

**Chosen:** `MIN_TRADES_FOR_CONFIRMATION = 30`, checked *after* the failure
gates, so it can only downgrade a would-be confirmation to `inconclusive`.

**This is the weakest-grounded number in the module and it is labelled as
such in the code.** Thirty is the conventional small-sample rule of thumb and
nothing more — there is no literature anchor behind it, unlike the grounding
prior below. It was flagged as the weak link before being written, and
approved as a deliberately-labelled placeholder.

**Why shipping it at this confidence is defensible:** the asymmetry bounds
the damage. Because the number can only ever move a verdict *toward*
caution, the direction of error is always safe. If 30 is wrong, the cost is
an over-cautious "insufficient evidence" on a real edge — never a confirmed
hypothesis that should have been killed. A wrong number that can only make
the system more conservative is a different kind of risk from one that can
make it more permissive, and that difference is what makes a provisional
value acceptable here and would not make it acceptable in the falsification
gate.

**Alternative considered:** derive a minimum from statistical power — the
number of trades needed to detect a given Sharpe at a given confidence.
Rejected for now because it needs an assumed effect size, which is just as
uncalibrated as 30 but *looks* rigorous, and false precision is worse than
labelled imprecision. The revisit trigger is recorded in the code: once a
charter has produced several confirmations, check whether windows in the
20-40 trade range behaved differently from windows well above it.

**Cost to reverse:** trivial, and expected.

### The grounding prior as an assumed search burden

**Chosen:** express the prior as an *effective number of tests*, not as an
alpha multiplier:

```python
effective_tests = hypothesis_count * TIER_SEARCH_BURDEN[grounding_tier]
threshold = BASE_ALPHA / effective_tests
```

with `local_corpus = 1.0`, `whitelist_search = 2.0`, `none = 10.0`.

**Why this framing and not a multiplier on alpha:** the arithmetic is
identical; the *meaning* is not. An alpha multiplier is a fudge factor —
there is nothing to argue with. "Effective tests" states a claim someone can
disagree with: *how many alternatives do we assume were implicitly searched
to produce this hypothesis?* architecture.md's own wording — an ungrounded
hypothesis "is closer to random search" — is exactly a statement about search
burden, so encoding it as one keeps the code and the document saying the same
thing.

**The reasoning behind each value**, recorded in the code comment: a
`local_corpus` hypothesis came from a curated paper, so the search that found
it happened outside this system, is documented by citation, and survived peer
review — no additional burden assumed. A `whitelist_search` hypothesis was
found by live search over academic domains, but nothing curated or verified
it, so a deliberately mild doubling. An ungrounded hypothesis gets an
order-of-magnitude marker meaning "assume roughly ten alternatives were
implicitly weighed."

**What is explicitly not claimed.** The `none` tier's threshold lands at
`0.005`, near Harvey, Liu & Zhu (2016)'s recommended strictness for factor
research (`t > 3.0`, roughly `p < 0.0027`). That is a **sanity check that the
number is not absurd — not a derivation.** HLZ is in the corpus and
retrievable, and it would have been easy to adopt their threshold directly
and claim it as grounded. That was rejected for a specific reason: the corpus
also carries Chen & Zimmermann (2022) *deliberately*, and its own manifest
note says it is there "giving balanced methodological grounding rather than a
one-sided view." Chen & Zimmermann argue publication bias is less severe than
HLZ claim. Picking one side of a disagreement the corpus was built to
represent honestly would be exactly the wrong move — and would have dressed
an arbitrary choice in a citation, which is worse than an arbitrary choice
labelled as one.

**Cost to reverse:** trivial in code. The revisit trigger is recorded: once
≥20 hypotheses have been tested under any charter, compare confirmation rates
by grounding tier — similar rates mean the penalty is too high, a far higher
ungrounded confirmation rate means it is too low. A canary test pins the
current values so they cannot change without the reasoning and the disclosure
changing with them.

### Telling the model the verdict before it writes

**Chosen:** the prompt opens with `VERDICT: REJECTED` and asks for an
explanation.

**Alternative considered:** give the model the evidence and the gate results
and let it write a narrative without being told the conclusion, on the
reasoning that a genuinely independent write-up is a weak second check on the
code's decision.

**Why rejected:** it converts an expository task into a judgment call, and
`.claude/rules/agent-honesty.md` is explicit that quantitative decisions by
LLM judgment are bugs. The "second check" framing is also illusory — if the
model disagreed with the code, the code would win anyway, so the only effect
would be occasionally producing a narrative that argues against its own
verdict field. And an agreeable model looking at a `+0.941` final fold has a
friendly story readily available; inviting it to reach for one is the exact
failure this project is built around preventing.

**Cost to reverse:** trivial, and it should not be reversed.

### `render_verdict.py` prints the mechanical decision before calling the LLM

A small but deliberate property of the script: the gates and the status are
printed **before** `render_verdict` is invoked. If the printed status and the
printed narrative ever disagree, the reader can see immediately which one the
evidence supports. The alternative — printing only the finished verdict —
would make the code-decided status and the model-written prose
indistinguishable in the output, which is precisely the distinction this
component exists to maintain.

---

## 4. Concepts introduced

**Pre-registration, and why it has to be mechanical.** In clinical trials,
researchers publish what outcome would count as failure *before* running the
study, so they cannot look at the results and construct a story that fits.
This project does the same thing: Component 4's hypothesis includes a
`FalsificationCondition` written before any testing. But pre-registration
only works if the condition is applied *mechanically* — a pre-registered bar
that a human (or an LLM) interprets after seeing results provides no
protection at all, because interpretation is exactly where the story gets
constructed. Hence `_fails_bar`: a comparison with no opinion.

**Why "mechanical" is not the same as "honest."** This is the deepest lesson
in the component. Code reading real numbers with a fixed rule *feels*
objective, but a human still chooses **which data the rule reads**. On this
project's real walk-forward study, the same pre-registered condition applied
to the same recorded numbers produces "not falsified" if scored on the final
out-of-sample window and "falsified" if scored across all of them. No
subjective judgment enters either version. The dishonesty, if it happened,
would live entirely in the scope decision — which is why that decision is
argued explicitly in section 3 rather than left as an implementation detail,
and why it is defended by tests rather than by intent.

**Multiple comparisons, and why an agent that generates its own hypotheses is
especially exposed.** If you test one hypothesis at a 5% significance
threshold, you accept a 5% chance of a false positive. Test twenty
independent nulls and you expect one "significant" result by chance alone.
This system generates its own hypotheses, so it can test as many as it likes
— which makes it structurally capable of manufacturing a false positive
simply by continuing. The correction divides the threshold by the number of
tests, so the bar rises as the search widens. What goes wrong without it: a
scoreboard that fills up with confirmed strategies, each individually
"significant," none of them real.

**Family-wise error rate versus false discovery rate (Bonferroni versus
Benjamini-Hochberg).** Bonferroni controls the probability of *any* false
positive among all tests, by dividing alpha by the number of tests. It is
simple, conservative, and — crucially here — computable one test at a time.
Benjamini-Hochberg instead controls the expected *proportion* of false
positives among the results you call significant; it is more powerful but
requires ranking the whole set of p-values together. That requirement is what
makes it inapplicable to sequentially-rendered verdicts and applicable to a
later scoreboard-wide re-evaluation, which is the split this component makes.

**Grounding as a prior.** In Bayesian terms, evidence updates a prior belief.
A hypothesis drawn from a peer-reviewed paper starts with a higher prior
probability of describing something real than one a model invented, because
someone else already did search and scrutiny that the invented one skipped.
This component encodes that as a search burden rather than as an explicit
prior probability, because the multiple-comparisons machinery already
operates in "number of tests" units and translating between the two would
add a conversion nobody could check.

**Defense in depth.** Independent checks that each catch a failure alone, so
that any one being wrong does not let the failure through. Section 5 shows
this arriving unplanned: narrowing the falsification gate's scope on real
data disarms that gate, and the verdict survives anyway because the mandatory
control fails independently. The value of the property is real; the danger is
that it can mask a broken check, which is why the scope test asserts on the
*gate result* and not only on the final status.

---

## 5. How Sacred Gate 2 was satisfied

This section is deliberately exhaustive. Gate 2 is one of the two
load-bearing claims of the entire project.

### The real evidence

Every number below came from a live run of Component 6's loop against real
cached AAPL data, recorded in `tool_call_traces` on 2026-08-23. The study is
a four-window walk-forward of the `LowVol_AAPL_ATR_MeanReversion` hypothesis.

| Window | Kind | Sharpe | p-value | Trades |
|---|---|---|---|---|
| 0 | in-sample | +0.771 | 0.432 | 73 |
| 1 | out-of-sample | **−1.510** | 1.000 | 4 |
| 2 | out-of-sample | +0.545 | 0.794 | 6 |
| 3 | out-of-sample | **+0.941** | 0.312 | 7 |

The hypothesis's own pre-registered bar, written in Component 4 before any
testing: *fails if `sharpe_ratio` less_than `0.5`*. Grounding tier
`whitelist_search`, one hypothesis under the charter, so the corrected
threshold is `0.05 / (1 × 2.0) = 0.025`.

Window 3 is why this study is a good test. It ends on `+0.941` — a
genuinely decent-looking number, and exactly the kind of ending an agreeable
system would build a hopeful narrative around.

### The killing half — what the code decided

All three gates fail:

```
[FAIL] pre_registered_falsification: out-of-sample windows failing: [1]
[FAIL] mandatory_control:            windows failing: [1, 2, 3]
[FAIL] sample_adequacy:              windows below 30 trades: [1, 2, 3]
=> STATUS: REJECTED
```

Printed **before any LLM call**. The narrative the model then wrote opens:
*"The hypothesis is dead."*

### The scope trap, and an honest correction

Before building this, the prediction recorded was that narrowing Gate 1 to
the final out-of-sample window would flip the real study from `rejected` to
`confirmed`. **That prediction was wrong, and the test caught it.**

What actually happens: narrowing the scope *does* flip the falsification gate
from FAIL to PASS — `+0.941` genuinely does not breach the `0.5` bar, so the
pre-registered condition is silently disarmed. But the overall verdict stays
`rejected`, because the **mandatory control fails on that same window
independently** (`p = 0.312` against a threshold of `0.025`).

That is defense in depth working, and it is a better result than the
prediction holding would have been — it means the verdict does not rest on a
single check. But it also means the real study **cannot by itself demonstrate
that scope changes a final verdict**, which is what the safeguard needed to
prove. So the test suite carries two tests instead of one:

1. `test_narrowing_gate1_to_the_final_window_flips_the_real_study` asserts on
   the **gate result**, not the status: scored across all out-of-sample
   windows the falsification gate fails; scored on the final window alone it
   passes. Same condition, same real data, opposite answer. It also records
   explicitly that the control is what saves this particular study, so the
   limit of the safeguard is visible rather than assumed away.
2. `test_scope_alone_can_decide_a_verdict` is **clearly labelled as
   constructed**, because the real data cannot make this point. Every window
   beats the control on deep samples; one middle out-of-sample fold breaches
   the bar. All-windows → `rejected`. Final-window → `confirmed`. Nothing
   differs but which data the mechanical rule was pointed at.

Mutating `decide_status` to read only the final out-of-sample window breaks
**three** tests.

### The fabrication half — five ways to try, all caught

| Attempt | Result |
|---|---|
| Claim with a dangling trace reference | rejected — "does not exist" |
| Claim whose value differs from its trace | rejected — reports both numbers |
| Claim citing a metric absent from its trace | rejected — "absent from trace" |
| Claim citing **another study's** real trace | valid in its own run, rejected in this one |
| Claim referencing an errored trace | rejected — "recorded an error" |
| Fabricated number in prose, claims clean | caught by the orphan-number scan |

The last row is the one `validate_claims` alone would miss, and the reason
the scan exists.

### Independent re-verification of the real verdict

After the verdict row was written, every stored claim was re-read from the
database in a **separate query** and compared against its trace — not
trusting the component's own report of success:

```
trace 13  sharpe_ratio claimed=0.7713661100394356   traced=0.7713661100394356   OK
trace 15  sharpe_ratio claimed=-1.5099349640345165  traced=-1.5099349640345165  OK
...
ALL CLAIMS RESOLVE TO REAL TRACES: True
```

Ten claims, ten matches. Five caveats stored: three code-generated mandatory
ones and two the model added, including a genuinely useful observation that
the strategy's entry conditions are rarely satisfied simultaneously.

### The live bug, and why it was diagnosable

The first live attempt failed validation three times and raised
`VerdictValidationError` with no detail beyond "failed after 3 attempts."
That message was useless — it could not distinguish a model fabricating from
a check being wrong.

So the exception was changed mid-debugging to carry `errors` and
`narrative`, and the answer appeared immediately:

```
- narrative contains '0.5', which matches no validated claim
- narrative contains '30', which matches no validated claim
```

The model had written *"requires all out-of-sample Sharpe ratios to be at
least 0.5"* and *"far fewer than the required 30 trades"* — both true, both
supplied by the system itself, and neither in the structural allowlist. **A
too-tight check rejecting honest prose, not a model fabricating.** The fix
was to add exactly those two constants — the pre-registered bar and the trade
floor — not to loosen the scan.

The lesson worth keeping: an error message that cannot distinguish "the model
lied" from "my check has a gap" is not a diagnostic, and in a component whose
entire purpose is telling those two apart, that gap was the wrong place to
economize.

### Mutation testing

Every guarantee was deliberately broken and the suite re-run:

| Mutation | Tests that caught it |
|---|---|
| Gate scope narrowed to final window | 3 |
| Sample adequacy checked before the failure gates | 3 |
| Claim value check removed | 1 |
| Control gate dropped from the status decision | 2 |

`verdict.py` was confirmed byte-identical to its pre-mutation backup
afterwards. Full suite: **312 passing**.

### What this does NOT prove — stated loudly

**Only the rejection path is proven.** The `confirmed` path has never run
against real data. Every real study executed in this project so far has been
rejected, and while the confirmation logic is unit-tested against constructed
evidence, no genuine edge has ever been confirmed end-to-end.

The sharpest way to say this: **an agent that rejected everything would pass
every test in this file.** Gate 2 has two halves — never fabricates, and
kills when the evidence says to — and the second half has a mirror image that
this component cannot test on its own: *confirms when the evidence says to.*
An over-skeptical agent is a different failure from an agreeable one, and it
is equally useless.

That gap is exactly what Stage 6's golden set exists to close, with planted
**true** hypotheses the agent must confirm alongside planted false ones it
must kill. Until that exists, the honest claim is: *this component provably
kills a hypothesis that deserves it, and provably refuses to state a number
it cannot source.* Not: *this component is correct.*

Two smaller residual risks. The tolerance in `_close` (1% relative) means a
claim could be off by up to 1% and validate — deliberate, because models
round, but it is a real if small window. And the orphan-number scan works on
numeric tokens, so a fabricated *non-numeric* claim ("the strategy
outperformed in every regime") passes untouched; nothing here checks
qualitative assertions.

---

## 6. Interview defense

**"Walk me through how you stop the agent from fabricating a number."** Every
quantitative claim in a verdict is a structured object carrying the id of the
tool call that produced it, the metric name, and the value. Code looks up
that trace, confirms it belongs to this study run, and compares the number.
A claim with a dangling reference, a mismatched value, a metric the trace
does not contain, or a reference to another study's trace is rejected — and
if any claim is rejected, no verdict row is written at all. Then, because
clean claims say nothing about prose, a second pass extracts every number
from the narrative and requires each one to resolve to a validated claim or a
small allowlist of structural constants. The model is never trusted to
self-report; the check runs against the database.

**"Why didn't you just use the Benjamini-Hochberg function you already
built?"** Because BH needs the full set of p-values ranked together, and
verdicts render one at a time as each study completes — hypothesis 3 of an
eventual 40 has no access to the other 37. So I use a Bonferroni-style
threshold on the count so far, which is sequential-safe and conservative, and
I store the raw p-values and the count in the verdict row so a proper
cross-charter BH re-evaluation can run later. That re-evaluation can demote
an earlier confirmation as evidence accumulates, which is exactly the
"previously believed, now decayed" behavior the architecture already
specifies for the scoreboard. The tool is not unused by oversight — it is
deferred to the layer where its input actually exists.

**Hard question: "Your verdict logic is deterministic code, so how could it
be dishonest?"** By choosing what the code reads. My real walk-forward study
ends on a Sharpe of +0.94. Apply the pre-registered condition to that final
window and it does not breach the bar; apply it to every out-of-sample window
and window 1's −1.51 falsifies it immediately. Same condition, same recorded
numbers, opposite answers, and no subjective judgment anywhere in either
version. That is why the scope decision is argued explicitly in the code and
defended by three tests rather than left as an implementation detail. I would
add that I got the follow-up wrong when I first reasoned about it — I
predicted narrowing the scope would flip the real study to confirmed, and it
does not, because the mandatory control rejects that window independently.
That is defense in depth, which is a better outcome than my prediction, but
it also meant the real study could not demonstrate the point on its own, so
there is a second clearly-labelled constructed test where scope alone decides
the verdict.

**Hard question: "You have one real verdict and it is a rejection. How do you
know the thing works?"** I do not, fully, and I would not claim otherwise.
What is proven is that the component kills a hypothesis that deserves killing
even when the study ends on a flattering number, and that it refuses to state
a number it cannot source — ten claims on the real verdict, each
independently re-verified against its trace in a separate query. What is not
proven is the confirmation path: it has never run on real data. The blunt way
to put it is that an agent which rejected everything would pass every test in
this file, and an over-skeptical agent is as useless as an agreeable one.
Closing that requires planted-true hypotheses the agent must confirm, which
is Stage 6's golden set — that is the next thing I would build, and it is the
strongest differentiator in the project precisely because it tests the half
this component cannot test itself.

**"Why is there a hard-coded 30 in the middle of your rigor code?"** Because
it is the one number here with no literature behind it, and I would rather
label that than dress it up. It is the conventional small-sample rule of
thumb. What makes shipping it defensible is the asymmetry: the gate is
checked *after* the failure gates, so too few trades can downgrade a
would-be confirmation to "inconclusive" but can never rescue a hypothesis the
evidence already killed. The direction of error is always toward caution — a
wrong value costs an over-cautious "insufficient evidence" on a real edge,
never a confirmed hypothesis that should have died. I considered deriving it
from statistical power instead, and rejected that because it needs an assumed
effect size that is just as uncalibrated but looks rigorous, and false
precision is worse than labelled imprecision. The revisit trigger is written
into the code.

**Honest weaknesses.** Only the rejection path has real evidence behind it.
The grounding-prior multipliers are reasoned but uncalibrated, and disclosed
as provisional inside every verdict the system writes. The 30-trade floor has
no anchor at all. The orphan-number scan catches fabricated numbers but not
fabricated qualitative claims. And the claim tolerance allows a 1% deviation,
which is deliberate but is a real if narrow window.

---

## 7. What comes next and why

**Stage 6 — the evaluation harness** is the direct successor, and this
component's honest limitation is what makes it necessary rather than
decorative. A golden set of hypotheses with known correct verdicts —
planted false ones the agent must kill, planted true ones it must confirm,
and known-caveat cases where the correct answer is "insufficient evidence" —
is the only way to test the half of Gate 2 that Component 7 cannot test
alone. Run on every agent change and continuously in production, it also
becomes the drift detector: when planted-false hypotheses start passing,
something has changed for the worse.

**Architecture.md Step 6 — the scoreboard** inherits the deferred
correction. The raw p-values and hypothesis count stored in every verdict row
exist so that a cross-charter Benjamini-Hochberg re-evaluation can run
without re-executing a single study, and can demote a previously confirmed
belief as evidence accumulates.

**If this component were wrong, here is how it would surface.** A broken
claim validator would fail loudly and visibly — verdicts would stop being
written, because validation failure means no row at all. The dangerous
failure is the quiet one: a narrowed gate scope produces verdicts that are
internally consistent, fully sourced, and confidently wrong, because every
number in them is real and only the question being asked has changed. Nothing
downstream would flag it. The scoreboard would fill with confirmed strategies
whose evidence, read individually, checks out perfectly. That is why the
scope decision is tested against real data with the exact numbers that make
it matter, and why those tests assert on gate results rather than only on
final status — a status assertion alone would have been silently rescued by
the control gate, and the trap would have gone unproven.
