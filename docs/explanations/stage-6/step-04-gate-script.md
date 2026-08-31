# Step 4 — Component 5, the Stage 6 Gate Script

## 1. What this does

`scripts/verify_stage6_gate.py` is Stage 6's own gate: "catches a
deliberately-broken agent." It ran live, **27/27 checks passed.** But it
took five real, live attempts to get there, and this document tells that
sequence in the order it actually happened — not because the process was
messy for its own sake, but because two of those five attempts found real
bugs in already-shipped, already-documented code, and the redesign in the
middle of the sequence exists entirely because of what those attempts
demonstrated, not because it was planned in advance.

Four files: `scripts/verify_stage6_gate.py` (built, then substantially
rewritten), `src/eval/resumable.py` (new — the resumability, pacing, and
fail-fast primitives), `tests/eval/test_resumable.py` (new — 12 tests,
all zero-cost), and two further changes to `src/eval/harness.py` beyond
what Component 2 shipped: `run_case`'s exception handling around
`render_verdict` was widened after a real bug, and a new `ScorableCase`
Protocol lets `_score` be reused against a second type it was never
originally written for.

**What this is not.** It is not a claim that 120 seconds is the correct,
calibrated pacing interval — it is a disclosed, deliberately generous
guess, stated as such. It is not a claim that `is_rate_limited`'s string
match is a robust classifier — it is a named heuristic. And it is not
evidence that this specific sabotage (`decide_status` always returning
`"confirmed"`) is the only failure mode worth testing — it is the one
`docs/architecture.md` names as the central worry, not an exhaustive
catalogue of ways this system could go wrong.

---

## 2. The five attempts, in order

### Attempt 1 — the first crash, and the fix that turned out to be too narrow

The first version of the gate script had no pacing and no resumability:
Job 1 ran all six cases live via `eval.harness.run_case`; Job 2 patched
`agentic_core.verdict.decide_status` to always return `"confirmed"` and
re-rendered each case's verdict against its already-completed
`study_run_id`, the same reuse trick `verify_stage5_gate.py`'s own Job 2
already established.

It hit a real AWS Bedrock `RateLimitError` starting on the third of six
cases. `golden_true_1` and `golden_true_2` passed; `golden_false_no_edge`,
`golden_false_fails_control`, `golden_false_breaches_bar`, and
`golden_caveat_thin_sample` all failed the same way.
`eval.harness.run_case`'s own internal exception handling caught these
correctly — no crash there, clean `CaseResult`s recorded. But the gate
script's own `run_sabotage` function had `except VerdictValidationError`
only around its `render_verdict` call, not a broader catch. When Job 2
hit the same rate limit, the whole script crashed with an unhandled
exception, before a single one of the six cases had been cleaned up.

Diagnosed by querying the real dev database directly: six orphaned
fixtures, two of them (`golden_true_1`, `golden_true_2`) actually sitting
at `hypothesis.status='confirmed'` with a `study_run` at `'completed'`,
the other four stuck at `'testing'`/`'running'`. Cleaned up via
`eval.fixtures.cleanup`, verified clean by direct query. Fixed by
widening `run_sabotage`'s exception handling to catch `Exception`
broadly, matching `run_case`'s own already-established pattern — the fix
comment names the principle that was missed: "one case's exception must
not take down the batch," `eval.harness.run_golden_set`'s own docstring,
applied here to code that had not yet learned it.

### Attempt 2 — the same symptom, a genuinely different and more interesting cause

Retried with the fix above. Hit the same rate limit again. This time the
full traceback told a different story: the `RateLimitError` originated
**inside `eval.harness.run_case` itself** — specifically inside
`render_verdict`, called from `run_case`'s own `except
VerdictValidationError` block, which catches nothing else. This was a
real bug in already-shipped, already-documented Component 2 code.
`step-02-harness.md` had explicitly defended the original narrow catch,
reasoning that "anything else escaping `render_verdict` is unexpected and
should surface as `run_case` failing outright... for an outer caller to
catch." That reasoning assumed an outer `try/except` would reliably see
it. It does not: the full traceback showed two **nested**
`ExceptionGroup`s — one from `mcp.client.session.ClientSession.__aexit__`,
one from `mcp.client.stdio.stdio_client`'s own internal `anyio` task
group — meaning the exception, even though it originated inside a
`try/except` nested several calls deep, escaped past every one of them on
its way out, because it propagated through an `async with` scope managed
by MCP's own transport, which wraps whatever surfaces at its boundary in
an `ExceptionGroup` regardless of where inside that scope it started.

Fixed properly this time, not patched around: `run_case`'s own contract
was rewritten to **"always returns a `CaseResult`; never raises."** The
`render_verdict` call gained a second, broader `except Exception` clause
after the specific `VerdictValidationError` one, and the function's own
docstring now states the contract explicitly, citing this incident by
name. A defensive (now technically redundant, but cheap and consistent)
per-case `try/except` was also added to the gate script's own
`run_healthy_baseline`, which — unlike `eval.harness.run_golden_set` —
had never wrapped its own call to `run_case` at all. Cleaned up the three
fixtures that got furthest this time (`golden_true_1`, `golden_true_2`,
`golden_false_no_edge`), verified clean.

### Before attempt 3 — looking for real evidence instead of guessing

Rather than guess a wait time from elapsed clock time, the natural next
question was whether AWS itself could confirm the throttle had cleared.
Both CloudWatch (`ListMetrics`) and Service Quotas
(`ListServiceQuotas`) returned `AccessDenied` — this project's own IAM
user (named, fittingly, `bedrock`) is scoped narrowly to just invoke the
model, which is good least-privilege practice and also means no
account-level dashboard is reachable with these credentials. The fallback
was a single, minimal, real probe call — `max_tokens=1`, a trivial
prompt, the direct `AnthropicBedrock` client, no tool-forcing — which
cost a fraction of a cent (8 input tokens, 1 output token) and succeeded
cleanly. That is real evidence the throttle had cleared, with one honest,
disclosed limit: Bedrock does not return the `anthropic-ratelimit-*`
headers the direct Anthropic API does, so the exact quota ceiling stayed
genuinely unknown — only "not currently exceeded, right now" was
confirmed, not "here is the number."

### Attempt 3 — the failure that was informative, and the near-miss that wasn't caught

With both fixes in place, retried again. The healthy phase reached 3/6
(`golden_true_1`, `golden_true_2`, `golden_false_no_edge` passed; the
other three hit the same rate limit a third time) — but this time both
fixes held. No crash. Clean `CaseResult`s recorded for every case, full
cleanup ran for all six, the database was verified clean, and the script
exited with code 0.

But the overall gate result correctly self-reported as **FAILED**
(18/22), and the reason is the important part. The sabotage phase ran
against whatever `study_run_id`s were actually available, which meant it
could only meaningfully sabotage-test `golden_false_no_edge` — the one
case that both completed its healthy run *and* had real evidence to
corrupt, and it correctly showed `actual=confirmed`, caught. The other
three non-confirm cases had no `study_run_id` at all (their healthy phase
never completed), so they scored `passed=False` "by default" — not
because the sabotage mechanism was ever actually exercised against their
evidence. The gate script's own naive assertion (`all non-confirm cases
now fail`) was technically true, but three of those four "true" results
would have been true regardless of whether `decide_status` was sabotaged
at all, since nothing was ever rendered for them to sabotage. Reporting
this as a valid gate pass would have overstated what the run actually
showed, and it was not reported that way.

This is the point where five explicit requirements were set before any
further retry: state the exact pause length and the reasoning behind it,
leaning generous rather than minimal, since wasted retries had already
cost more than any plausible wait; prove the pacing logic actually works,
for free, before it ever touches Bedrock again; confirm the script stops
cleanly on a repeat rate limit instead of burning money on cases likely
to fail the same way; support resuming from exactly the cases that still
need it, skipping the ones that already succeeded; and confirm a resumed
case is rebuilt completely fresh, never patched together from partial
data, so it is exactly as trustworthy as a first-time success. None of
this was planned in advance. It is a direct, point-by-point response to a
demonstrated, real failure — stated here plainly rather than presented as
if it had been the design from the start.

### The resumability redesign

`src/eval/resumable.py` — generic and gate-script-agnostic on purpose.
Nothing in it knows about `GoldenCase`, MCP, or Bedrock, which is exactly
what makes its core logic testable with fakes in milliseconds rather than
needing a faked MCP `ToolSession` and a faked LLM just to prove a sleep
function gets called the right number of times.

```python
class ResumeRecord(BaseModel):
    name: str
    category: ...
    expected_status: ...
    expected_caveat_substring: str | None = None
    ticker: str
    charter_id: str
    hypothesis_id: str
    design_id: str
    healthy_passed: bool
    healthy_detail: str
    study_run_id: str | None = None
    sabotage_done: bool = False
    sabotage_passed: bool | None = None
    ...
```

Deliberately not `GoldenCase`. A case that already succeeded never again
needs the full `Charter`/`Hypothesis`/`StudyDesign` Pydantic objects —
those exist only to drive a live execution loop, and a case with a
completed `study_run_id` never runs that loop a second time. Persisted to
`reports/stage6_gate/resume_state.json` (a new, gitignored path) via
`load_resume_state`/`save_resume_state`, written after *every* case in
*both* phases, not just at the end.

```python
def resume_action(existing: ResumeRecord | None) -> Literal["skip", "cleanup_and_retry", "build_fresh"]:
    if existing is None:
        return "build_fresh"
    if existing.healthy_passed:
        return "skip"
    return "cleanup_and_retry"
```

Three outcomes, never a fourth. No record → build fresh. Already
succeeded → skip, untouched, exactly as trustworthy as it always was.
Previously failed → clean up its leftover rows, verify clean, then
rebuild completely from scratch and re-run in full — never resumed
mid-loop. That last part is not a cautious choice invented for this
component; it is Stage 5's own architecture, already decided: no
checkpointer is configured, and `step-07-execution-loop-state.md` already
states the consequence plainly — "a crashed run is not resumable
mid-step... it is re-run from scratch or left 'failed'." This module does
not work around that decision. It is built to respect it, which is why
there is no fourth action here that reuses a half-finished attempt.

```python
async def run_with_pacing(items, process, pace_seconds, sleep_fn) -> int:
    processed = 0
    for item in items:
        if processed > 0:
            await sleep_fn(pace_seconds)
        should_stop = await process(item)
        processed += 1
        if should_stop:
            break
    return processed
```

The generic pacing-plus-circuit-breaker loop. Waits before every item
except the first attempted — never before the first (nothing to protect
yet), never after the last (nothing left to protect). Stops immediately,
with no further items even attempted, the moment `process` returns
`True` — the signal a caller uses for "this is fatal, do not continue,"
distinct from an ordinary per-item failure that's still worth continuing
past (which returns `False` and the loop moves on). `sleep_fn` has **no
default** — a deliberate choice: a default of `asyncio.sleep` would make
it dangerously easy for a future test to accidentally inherit a real
120-second wait without ever noticing, since "call the function with no
arguments" would silently do the wrong thing instead of raising a clear
`TypeError` demanding the caller decide.

```python
def is_rate_limited(detail: str) -> bool:
    return "RateLimitError" in detail
```

A plain string match against a `CaseResult`'s own `detail` field, and the
function's own docstring says so explicitly: this is a heuristic, not a
typed signal. A differently-worded throttle error, or a similar condition
raised as a different exception class by a future SDK version, would not
trip it. This is a deliberate, disclosed trade — a gate-script circuit
breaker deciding whether to keep spending real money is not a
safety-critical classifier, and a heuristic honestly named as one is more
trustworthy than a falsely precise-looking abstraction wrapped around the
same string check.

`PACE_SECONDS = 120.0`. Three independent real hits all landed roughly a
third to half of the way through an unpaced six-case burst — somewhere
around 15 to 25 calls. That is consistent with a rolling per-minute
ceiling in that neighborhood; it is not proof of one, since the exact
quota stayed unreachable (Section 2's "before attempt 3" already
established why). 120 seconds is double a typical 60-second window,
chosen deliberately generously: the only cost of overshooting is
wall-clock time, and the cost of undershooting had already been paid
three times over in real dollars.

`eval.harness.py` gained one more addition alongside `resumable.py`:

```python
class ScorableCase(Protocol):
    name: str
    category: Literal[...]
    expected_status: Literal[...]
    expected_caveat_substring: str | None
```

`_score`'s type hint moved from `GoldenCase` specifically to this
Protocol. Both `GoldenCase` and `ResumeRecord` satisfy it structurally —
neither has to inherit from the other, and `_score`'s three-line scoring
rule is not duplicated a second time for the sabotage phase's own
re-scoring. This is what lets the gate script call `_score(resume_record,
study_run_id, corrupted_verdict, detail)` directly against a persisted
`ResumeRecord`, using the exact same, already-tested logic
`tests/eval/test_harness.py` already exercises against a `GoldenCase`.

`tests/eval/test_resumable.py` — 12 tests, every one a plain synchronous
test function that calls `asyncio.run()` directly on a small async body,
deliberately not `@pytest.mark.anyio` or any async pytest plugin, to
avoid adding new test infrastructure for a need this simple. Covers
`resume_action`'s three branches, `is_rate_limited`'s positive and
negative cases, `run_with_pacing`'s core pacing behavior (sleeps between
items, never around them), its stop-on-signal behavior (items after the
signal are never even attempted), an explicit test that an *ordinary*
`False` signal does not stop the batch (distinguishing a recoverable
per-item failure from a fatal one), an explicitly named test proving no
test in this suite ever really waits (`pace_seconds=3600`, completes
instantly), an empty-list edge case, and the same `tmp_path` JSON
round-trip pattern `test_harness.py` already established for
`GoldenSetReport`.

### Attempt 4 — a new, self-inflicted bug, on the very first live use of the new design

Healthy phase ran clean: 6/6, no rate limit this time. (Stated honestly:
this one clean run cannot by itself distinguish "the pacing worked" from
"got lucky this time" — a single pass at a new interval is consistent
with the pacing being sufficient, not proof that it always will be.)

Then, on the very first sabotage case, a new assertion failed:
`decide_status restored after sabotaging golden_true_1` — `False`. Found
for free, before spending anything further: the restoration check itself
was placed **inside** the `with patch(...):` block instead of after it.

```python
with patch("agentic_core.verdict.decide_status", _always_confirms):
    try:
        ...
    except ...:
        ...
    restored = verdict_module.decide_status is real_decide_status  # BUG: still inside the block
```

The patch is, by definition, still active anywhere inside that block —
so this check could never have reported `True`, regardless of whether
`unittest.mock.patch` actually restores correctly (it does). Verified
with a ten-line, zero-cost standalone snippet using
`types.SimpleNamespace` and `patch.object` — no Bedrock, no MCP, nothing
related to this project at all — showing the identical check reports
`False` when placed inside the `with` block and `True` when placed
outside it, on trivial fake functions. This is the same genre of lesson
`step-10-gate-script.md` already named for Stage 5's own gate script: a
failing assertion names a disagreement between expectation and
observation, and the discipline is to find out which side is actually
wrong rather than assume the more dramatic explanation. Here, as there,
it was the test's own code, not the mechanism under test.

Fixed by dedenting the `restored = ...` line to after the `with` block.
Verified for free before resuming — the same standalone snippet, now
showing the fixed placement reports `True` correctly.

### Attempt 5 — the resume, proven for real

All six cases printed `[SKIP — already succeeded]` immediately.
Attempt 4's `resume_state.json` had already recorded all six as
`healthy_passed=True` with real `study_run_id`s from that run's own
healthy phase, so the entire expensive part — six live execution loops,
roughly $0.34 of real Bedrock spend — was correctly skipped rather than
re-run. This is the moment resumability proved itself against the real
system, not just against `tests/eval/test_resumable.py`'s fakes.

The sabotage phase then ran cleanly against all six. `decide_status
restored` reported `True` after every single one of the six sabotage
attempts — not once for the whole batch, as the original design checked,
but six independent confirmations, at zero additional cost, because
fixing the placement bug also surfaced the chance to check it more often
rather than merely correctly. And this time, all **four** non-confirm-
expected cases genuinely flipped to `actual=confirmed`, `passed=False`
under real sabotage — compare attempt 3's partial, partly-vacuous result,
where only one of the four was ever actually tested. Final tally: 27/27
checks passed. Cleanup ran and was verified for all six. The
`resume_state.json` file was deleted, per design — its references are
stale the instant cleanup runs, and leaving it behind would make a future
invocation skip cases whose rows no longer exist.

**Stage 6 gate: PASSED.**

The full project test suite — 340 tests, up from 328 at the start of this
component (the 12 new ones are `test_resumable.py`) — was reconfirmed
green after every code change in this sequence, not just at the end.

---

## 3. Design decisions and rejected alternatives

### `resumable.py` is generic, not gate-script-specific

**Chosen:** no `GoldenCase`, no MCP, no Bedrock anywhere in this module.

**Alternative considered:** write the pacing and resume logic directly
inside `scripts/verify_stage6_gate.py`, since (at least today) nothing
else uses it.

**Why rejected:** the entire reason `run_with_pacing` and `resume_action`
could be tested in milliseconds with fakes is that they know nothing
about what they're pacing or resuming. Writing the same logic inline,
coupled to `GoldenCase`/`run_case`/MCP, would mean testing it properly
requires a faked `ToolSession` and a faked LLM just to verify that a
sleep function gets called the right number of times — real, avoidable
machinery for a property that has nothing to do with any of those things.
This also means `eval.harness.run_golden_set`, which has no pacing or
resumability of its own yet, could adopt these same primitives later
without needing to import anything gate-script-specific.

**Cost to reverse:** low — the module has one real caller today. Inlining
it would be a small mechanical change, at the cost of losing the cheap
tests that currently exercise it.

### `sleep_fn` has no default

**Chosen:** every call to `run_with_pacing` must supply `sleep_fn`
explicitly.

**Alternative considered:** default it to `asyncio.sleep`, so production
call sites don't have to name it.

**Why rejected:** a default would make the dangerous case — a future test
that forgets to pass a fake — silently do the wrong thing (wait 120 real
seconds per gap, without erroring, without complaint) instead of the
safe thing (a `TypeError` demanding the caller decide). Given this
module exists specifically because a live rate limit already cost real
money three times, making the untested path the *quiet* failure mode
rather than the *loud* one would be exactly backwards.

**Cost to reverse:** trivial in code, and there is a real reason not to.

### Healthy-phase retries clean up first; sabotage-phase retries do not

**Chosen:** `resume_action`'s `cleanup_and_retry` branch — deleting
leftover rows before rebuilding — is only ever exercised for the healthy
phase. A failed sabotage attempt is simply retried with no cleanup step
at all.

**Alternative considered:** treat both phases symmetrically, with the
same clean-then-rebuild step before any retry.

**Why rejected:** the two phases fail differently, for a reason
`step-02-harness.md` already introduced as a general hazard —
non-atomic, multi-step construction. `eval.fixtures.seed_price_bars` and
the charter/hypothesis/design builders each commit independently, so a
partial healthy-phase failure really can leave partial rows behind that
need deleting before a clean retry. `render_verdict`, by contrast, is
all-or-nothing: it either writes one fully-validated `Verdict` row after
every claim passes validation, or it raises having written nothing at
all. There is no partial state a failed sabotage attempt could leave
behind, so a cleanup step before retrying it would be doing real work to
guard against a failure mode that structurally cannot occur. This is the
same concept doing new work: previously a hazard to defend against, now
also the reason two outwardly similar retry paths are legitimately
different.

**Cost to reverse:** trivial, but reversing it would add a no-op cleanup
call to a path that has never needed one.

### The restoration check moved from once-per-batch to once-per-case

**Chosen:** `decide_status is real_decide_status` is checked, and
asserted, after every single sabotaged case.

**Alternative considered (the original design, attempts 1-3):** check it
once, after the whole sabotage batch completes.

**Why the stronger version is better, not just fixed:** the bug that
forced revisiting this code (checking inside the `with` block instead of
after it) would have existed either way — but once the fix required
touching this code regardless, checking after every case instead of once
for the whole batch costs nothing extra and produces six independent
confirmations that the patch cleanly reverts instead of one. The
`AssertionError` this check exists to catch, if it ever fires for real,
now identifies exactly which case's sabotage attempt left the patch in a
bad state, rather than only "somewhere in this batch."

**Cost to reverse:** trivial, and there is no reason to.

### `ScorableCase` as a Protocol, not a shared base class

**Chosen:** `_score`'s parameter type is a structural `Protocol`
declaring the four fields it actually reads.

**Alternative considered:** make `ResumeRecord` inherit from `GoldenCase`,
or extract a shared base class both inherit from.

**Why rejected:** `GoldenCase` and `ResumeRecord` exist for genuinely
different purposes — one carries everything needed to drive a live
execution loop (`charter`, `hypothesis`, `design`, all real Pydantic
objects); the other carries everything needed to persist and resume
across process invocations (ids, a `study_run_id`, sabotage results) and
deliberately does *not* carry those three objects, because a resumed case
never needs them again. Forcing an inheritance relationship between two
types with that little in common would couple their futures together for
no real benefit — a change to `GoldenCase`'s own fields for loop-driving
reasons would have no business affecting `ResumeRecord`'s own shape.
Structural typing gets the actual thing needed (both types happen to
carry the same four scoring fields) without inventing a relationship that
doesn't otherwise exist.

**Cost to reverse:** low, but there is no reason to prefer the
alternative even in principle here.

---

## 4. Concepts introduced

**A rolling rate-limit window, inferred from failure location rather than
documentation.** Without access to AWS's own quota dashboards, the
*shape* of three independent failures — each landing roughly a third to
half of the way through an identical, unpaced sequence — is itself real
evidence about where a resource ceiling sits, even without a number from
the provider. This is a weaker form of evidence than reading the actual
quota, and the document says so, but it is not nothing: an estimate
grounded in repeated, consistent real observation is different from an
estimate grounded in nothing at all.

**Exception propagation across an `async with` scope managed by a task
group.** `step-02-harness.md` already introduced the mechanism (a plain,
synchronous exception raised inside code nested within an `anyio`-based
task group's scope can surface at that scope's own exit, wrapped in an
`ExceptionGroup`, regardless of which `try/except` blocks are nested
between the raise site and that boundary). This component is the second
time that exact mechanism defeated a `try/except` that looked correctly
placed — the lesson generalizes further with the second occurrence: a
`try/except` inside such a scope is not a *sufficient* guarantee just
because it syntactically wraps the call that raises; if the exception
type genuinely propagates through the surrounding scope's own machinery
rather than being caught cleanly at the point it's raised, the "obvious"
fix (widen the except clause where the exception is raised) is necessary
but must be verified live, because the failure mode is about *scope*, not
just exception *type*.

**Distinguishing "the mechanism is broken" from "the check of the
mechanism is broken."** Attempt 4's failed assertion looked, on its
surface, like the most serious possible finding for this component —
that `unittest.mock.patch` doesn't actually restore what it patches,
which would undermine the entire safety argument for the sabotage phase.
It was not that. A ten-line, zero-cost, fully isolated reproduction —
deliberately using types with no relationship to this project at all —
settled the question before any further live spend was risked, the same
discipline `step-10-gate-script.md` already named: read what the *other*
evidence already shows (here, that `patch` is one of the most heavily
used and tested primitives in the entire Python standard library, making
"the check is wrong" the far more likely diagnosis than "the standard
library is wrong") before concluding the more dramatic explanation.

---

## 5. How Stage 6's own gate was satisfied — and what it does not prove

**What passed, live, for real:** all six golden-set cases completed their
real execution loop and reached their declared verdict (Component 2's own
achievement, reconfirmed here). All six were then re-rendered with
`agentic_core.verdict.decide_status` forced to always return
`"confirmed"`, and every one of the four cases whose real evidence says
otherwise — three `"rejected"`, one `"inconclusive"` — correctly stopped
passing, each independently, with the patch's own restoration confirmed
after every single one. That is the literal text of Stage 6's gate,
satisfied on real evidence: a deliberately broken agent gets caught, not
waved through.

**What this does not prove.** This tests exactly one failure mode — an
agent that always confirms, chosen because `docs/architecture.md` names
it as the central worry, not because it is the only way this system could
fail. A different break (a loosened `mandatory_control` threshold, a
flipped comparison direction in `decide_status`, a `validate_claims`
check silently disabled) is not exercised here at all, and nothing in
this component's passing result says anything about whether the harness
would catch those. Sacred Gate 2's own Stage 5 record already states the
matching limitation honestly for the confirm side (`stage-5-summary.md`:
"one real confirmed hypothesis, and it's synthetic"); this gate's own
honest limitation is the mirror of that on the harness side — one
sabotage, chosen deliberately rather than exhaustively.

`is_rate_limited`'s heuristic nature is itself a residual risk worth
naming here rather than only in Section 3: if a future Bedrock SDK
version reports throttling under a different exception class or a
differently-worded message, this circuit breaker would not recognize it,
and the gate script would go back to the crash-prone behavior attempts 1
and 2 already demonstrated — mitigated, but not eliminated, by `run_case`
and `run_healthy_baseline` both now independently guaranteeing they never
raise regardless of what caused the failure.

---

## 6. Interview defense

**"Walk me through why this took five attempts to pass, not one."**
Attempts 1 and 2 found two real bugs in exception handling — one in the
gate script itself, one in already-shipped Component 2 code whose
original design had explicitly, deliberately reasoned itself into a gap
that only a live rate limit exposed. Attempt 3 hit the same external rate
limit a third time, but this time both fixes held, and the failure
surfaced a real, subtler problem: a technically-true assertion
(`all non-confirm cases now fail`) that would have been a misleading pass,
because three of the four cases it covered never had real evidence to
sabotage in the first place. That's the point a genuinely new capability
— pacing, a circuit breaker, and full resumability — got built, tested
for free before touching Bedrock again, and then immediately found its
own new bug on first live use (attempt 4), fixed for free with an
isolated reproduction, and finally passed completely and honestly on
attempt 5. Five attempts is not a story about carelessness; it's a story
about a system getting genuinely more correct at every single step,
verified at each one rather than assumed.

**"Why didn't you just add a fixed `time.sleep()` between calls inside
`llm_client.structured_output` itself, instead of building a whole
resumability module?"** Because that module's own no-retry contract is a
deliberate, already-documented Stage 5 decision (`llm_client`'s own
module docstring: "Single call, single validation attempt, raise on
failure... this is permanent rather than pending"), and burying a
pacing delay inside the one function every LLM call in this project goes
through would silently slow down every caller, including ones that never
hit this rate limit at all, for a problem specific to this one script's
own calling pattern (six cases, back-to-back, no natural pause between
them). Pacing belongs at the orchestration layer that actually creates
the burst, not inside the primitive that has no idea it's being called in
one.

**Hard question: "You built an entire resumability system, with a
persisted JSON file and a three-way retry decision, for a script that
runs once to close one stage's gate. Isn't that overbuilt for what it's
for?"** I'd have agreed with that critique before attempt 3. What changed
my mind is that this wasn't hypothetical scale-proofing — it was a
direct, demonstrated response to hitting the exact same real, external
failure three times in a row, each time spending real money and making
partial, hard-to-interpret progress. A script that has already failed
non-deterministically three times, live, against infrastructure I don't
fully control, is not a one-shot script anymore in practice, whatever it
was designed to be — and building the ability to resume from exactly
where it stopped, rather than continuing to pay for a full six-case
re-run on every attempt, is the proportionate response to what actually
happened, not speculative engineering for a future that might not come.
It also cost very little extra: the primitives are generic enough that
`eval.harness.run_golden_set` could adopt the same pacing later with no
new design work.

**"Your circuit breaker is a string match on an error message. What
happens when that breaks?"** It goes back to being exactly as safe as it
was in attempts 1 and 2, before this component existed — `run_case` and
the gate script's own per-case guards still catch the exception and
record a clean failure, they just wouldn't proactively *stop* the batch
early anymore, so a few more cases might be attempted (and likely fail
the same way) before the whole thing finishes. That's a real, disclosed
limitation, not a silent one — the honest answer is "it degrades to
attempts 1-2's behavior, which was already crash-free by that point," not
"it would crash."

---

## 7. What comes next and why

**Stage 6 is complete.** Five components: the golden-set fixtures
(Component 1), the harness that drives them live (Component 2), the
zero-cost test suite for both (Component 4), and this gate script
(Component 5) — `scripts/run_golden_set.py` (Component 3) is the thin
entry point that ties the harness together for ordinary, non-gate
invocations. `docs/explanations/stage-6/stage-6-summary.md` synthesizes
all five next, closing the stage the same way `stage-5-summary.md` closed
Stage 5.

**If this component were wrong** — if the sabotage genuinely didn't get
caught, or if the resumability system silently corrupted state across
invocations — the failure would show up exactly where Stage 6's own
purpose says it should: a future change to `agentic_core/verdict.py`
that quietly breaks `decide_status` would pass this same gate script
undetected, and nothing else in this project would catch it, because this
is the one component whose entire job is catching exactly that. That is
also why this gate, unlike a one-time proof that never runs again, is
meant to be re-run whenever the agentic core changes — `docs/
architecture.md`'s own framing for the whole golden set, and the reason
Stage 8's scheduled infrastructure is where "run this continuously" stops
being a manual discipline and becomes an actual guarantee.
