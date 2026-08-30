# Step 2 — Component 2, the Golden-Set Harness

## 1. What this does

`src/eval/harness.py` is what actually runs Component 1's six fixtures.
Nothing before this component had ever driven a `GoldenCase` through the
real system — `step-01-golden-cases.md` says so explicitly in its own
verification section: two layers of proof (real `run_backtest`/
`test_significance` numbers, real database persistence) but neither one
runs a fixture through the real execution loop or `render_verdict`. This
component closes that gap, and — after a real bug, found and fixed live —
it closes it successfully: all six fixtures now reach their declared
verdict through the actual, Bedrock-driven agent, not just through
pre-computed numbers.

Three files: `src/eval/harness.py` (new — `_score`, `run_case`,
`run_golden_set`), `scripts/run_golden_set.py` (new — the thin,
manually-invocable entry point; genuinely ten lines with no design
decisions beyond what `harness.py` already made, so it's covered here
rather than in its own document), and `src/llm_client/__init__.py`
(modified — one new optional parameter, `on_usage`, needed to measure real
cost before committing to a full run).

**What this is not.** It is not Stage 6's own gate proof
(`scripts/verify_stage6_gate.py`, which deliberately breaks the agent and
confirms the harness notices — a genuinely different claim from "the
harness works on a correctly-functioning agent"). It is not a claim that
six passing cases makes this project's reliability question answered —
`stage-5-summary.md`'s own "sample size of one" criticism was about
*trusting* one confirm case; six cases across three categories is more
evidence, not a different kind of certainty. And the real bug covered in
Section 2 below means this component's own first live run was a failure,
not a success — the story matters as much as the outcome.

---

## 2. Every meaningful line explained

### `_score` — pure, no I/O

```python
def _score(case: GoldenCase, study_run_id: str | None, verdict: Verdict | None, detail: str) -> CaseResult:
    actual_status = verdict.status if verdict is not None else None
    status_correct = actual_status == case.expected_status
    fabrication_clean = verdict is not None
    caveats_ok = case.expected_caveat_substring is None or (
        verdict is not None and any(case.expected_caveat_substring in c for c in verdict.caveats)
    )
```

This function touches no database, no LLM, no network — it takes whatever
a run produced (or failed to produce) and computes three booleans. This is
the same split `agentic_core.verdict.decide_status` makes against the
loop's own nodes (`stage-5-summary.md` Section 3: "test the code, not the
live system, for properties that are about code"), applied one level up:
`decide_status` is pure and mutation-tested against hand-built
`WindowEvaluation`s in `test_verdict.py`; `_score` is pure and will be
mutation-tested the same way against hand-built `Verdict` objects in
`tests/eval/test_harness.py` (Component 4). Only `run_case`/`run_golden_set`
below need a live system to trust.

`fabrication_clean = verdict is not None` is a narrower claim than it
might look. It does not mean "no fabrication was detected" — it means "a
verdict exists at all." Fabrication detection already happened, inside
`render_verdict` itself, before this function ever runs: a verdict that
made it out of `render_verdict` already survived `validate_claims` and
`scan_for_unreferenced_numbers`. `verdict is None` covers two distinct
paths that both deserve the same score — the execution loop never reached
`status='completed'`, or it did but every retry attempt inside
`render_verdict` failed validation and `VerdictValidationError` fired —
and both are scored identically as "not fabrication-clean," because
neither one leaves anything to certify as clean. There is no partial
credit; `False` is the only honest default when the thing being asked
about does not exist.

`caveats_ok`'s `case.expected_caveat_substring is None or (...)` ordering
matters: for five of the six cases, `expected_caveat_substring` is `None`,
and the clause short-circuits to `True` without ever touching `verdict` —
which is exactly right, because those five cases have nothing to check and
should not be penalized for a verdict that (correctly) never mentions a
caveat that was never required.

### `run_case` — impure, drives the real system

```python
async def run_case(case, session_provider, llm=structured_output) -> CaseResult:
    ...
    graph = build_graph(session_provider, llm, design_id=case.design_id, hypothesis_id=case.hypothesis_id)
    final = await graph.ainvoke(initial_state(case.charter, case.hypothesis, case.design))
    study_run_id = final["study_run_id"]
    loop_status = final["status"]
    ...
    if loop_status == "completed":
        try:
            _, verdict = render_verdict(study_run_id, llm=llm)
        except VerdictValidationError as e:
            detail_parts.append(...)
```

This is the exact same call shape `scripts/verify_stage5_gate.py::run_confirm_path`
already proved live for `GATE5PROBE`: real `build_graph`/`initial_state`,
real Bedrock choosing every tool call via the injected `llm`, then real
`render_verdict`. `render_verdict` is only called when `loop_status ==
"completed"` — its own guard already raises `ValueError` on anything else
("a verdict is only written for a completed run... a verdict drawn from
partial evidence is exactly what this component exists to prevent"), and
calling it anyway would just convert a clean, informative "loop didn't
finish" detail into a confusing exception from a function whose job isn't
to explain that. The `try/except VerdictValidationError` around
`render_verdict` is the one place this function narrows its exception
handling from the broader `except Exception` around the loop call — a
`VerdictValidationError` is a known, meaningful outcome (this exact case's
evidence didn't validate after every retry), while anything else escaping
`render_verdict` is unexpected and should surface as `run_case` failing
outright rather than being silently absorbed here.

### `run_golden_set` — the full lifecycle, and where the real bug lived

```python
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        for builder in builders:
            case = builder()  # ORIGINAL (buggy) version had no try/except here
            try:
                result = await run_case(case, lambda: session, llm)
            except Exception as e:
                result = CaseResult(..., detail=f"run_case raised unexpectedly: {e!r}")
            finally:
                cleanup(case.ticker, case.charter_id, case.hypothesis_id)
                ...
```

One MCP subprocess and one `ClientSession`, opened once, reused across all
six cases — not six separate launches. The MCP tools are thin wrappers
over pure functions reading the database plus their own arguments; there
is no server-side state that could leak between cases, and each case gets
its own fresh `build_graph()`/`initial_state()` call, so nothing about one
case's run is visible to the next. Launching a subprocess per case would
only add repeated startup cost (Component 6b's own measurement: importing
pandas, pandas-ta, and torch on every launch) with zero isolation benefit.

`run_golden_set` owns the entire build → run → cleanup lifecycle for every
case, the same shape `verify_stage5_gate.py` already uses for one fixture,
looped six times. The alternative — accepting pre-built `GoldenCase`
objects from the caller, so building and running are fully decoupled —
was considered and rejected: cleanup still has to happen right after each
case's run regardless of who built it, so decoupling would only move the
loop that calls the six builders into the thin script, without changing
who is responsible for the thing that actually went wrong here.

`case = builder()` shown above with its **fixed** guard is the second,
corrected version. The original had no guard at all. Section 3 covers
exactly why that was wrong, in full — it is the most important content in
this document.

### The `on_usage` hook — `src/llm_client/__init__.py`

```python
def structured_output(
    prompt: str, response_model: type[T], ...,
    on_usage: Callable[[Usage], None] | None = None,
) -> T:
    ...
    response = client.messages.create(...)
    if on_usage is not None:
        on_usage(response.usage)
    tool_use_block = next(...)
```

Before this component, `structured_output` discarded the raw API response
entirely on success — it returns only the validated `response_model`
instance, and the only path that ever exposed `response.usage` was
`StructuredOutputError.raw_response` on *failure*. There was no way to
learn what a successful call actually cost in tokens, which is precisely
what "run the whole golden set once before we do it, not discover it
after the fact" required.

`on_usage` is additive, not a second return value: every existing caller
(three, per this module's own docstring — `charter.py`, `hypothesis.py`,
`study_design.py` — plus the loop and `render_verdict`) passes nothing and
sees zero behavior change, because the default is `None` and the check
short-circuits. It fires **unconditionally**, right after
`client.messages.create()` returns, *before* the tool-use/validation
checks below it — not only on the success path. This is deliberate:
Bedrock bills for the call whether or not the response goes on to
validate, so a usage observer that only fired after a clean parse would
silently undercount every call that ended in `StructuredOutputError`.

The alternative considered — a separate wrapper callable that reimplements
`structured_output`'s own request-building logic (build the
`AnthropicBedrock` client, call `messages.create` with the same
tool-forcing shape, inspect `response.usage` directly) just to avoid
touching this module at all — was rejected for the same reason
`docs/explanations/stage-5/step-08-live-execution-loop.md`'s `TracingLLM`
gives for wrapping rather than editing `decide_next_action`: *"the loop's
behavior must be identical whether or not anyone is watching it."* A
parallel reimplementation is not the thing that actually runs in
production — it is a second copy of it, free to drift out of sync (a
future change to `structured_output`'s tool schema, retry semantics, or
error handling would silently stop being reflected in the measurement
tool). Hooking the one real call site, non-invasively, is the only way to
guarantee the thing being measured is the thing that runs.

### What was skipped

Genuine boilerplate: `_write_report`/`_print_summary` (straightforward
JSON serialization and a formatted print loop, no design decisions beyond
what Section 3 covers for `construction_errors`), and
`scripts/run_golden_set.py`'s own body (`asyncio.run(main())` plus an exit
code derived from `report.passed == report.total` — nothing else lives
there by design, so that every future consumer — a CI step, later a
scheduled job — gets the same behavior without this script changing).

---

## 3. Design decisions and rejected alternatives

### The real bug: `case = builder()` had no guard, and why that was wrong

**What was chosen, originally:** `case = builder()` sat outside the
per-case `try/except`, on the explicit reasoning (stated in this
function's own first-draft docstring) that "a builder failing means a
fixture logic bug, not a runtime case failure" — mirroring the same
argument `step-01-golden-cases.md` makes for skipping Components 2–4's own
LLM calls in fixture construction.

**Why that reasoning was incomplete:** it conflated two different kinds of
failure a builder call can have. A *logic* bug in a builder — a malformed
`StrategyRule`, an inconsistent date range — is deterministic and would
fail identically every time, so catching it wouldn't help; that part of
the original reasoning still holds. What it missed is that `builder()`
also performs real, **non-atomic** database writes:
`eval.fixtures.seed_price_bars` commits immediately, and the
charter/hypothesis/design rows commit separately afterward via
`build_charter_and_hypothesis`/`build_study_design`. A failure partway
through this sequence is an **operational** failure — about the state of
the database at the moment the call happens, not about the builder's own
code — and operational failures are exactly the kind a batch runner needs
to survive without losing the rest of the batch.

**What actually happened, in order, across three real live runs:**

Run 1 crashed partway through `golden_false_fails_control`, mid-execution
— its `Hypothesis` row was left at `status='testing'`, meaning the loop's
own `initialize` node had already run (fixture construction succeeded,
execution had begun) but `render_verdict` was never reached. The specific
root cause of *this* crash could not be determined after the fact,
because the live terminal output was truncated and never saved to a file
— an operational lesson in itself, named directly rather than glossed
over: redirect a live run's output to a file *before* running it, not
after it has already failed once and the output is gone.

Because that output was unreadable, the response was to immediately
re-run the entire six-case batch a second time, **without first checking
whether the crashed first run had left anything behind in the database.**
This was the real mistake in this component's history, and it is stated
as one rather than rationalized: the correct move — checking database
state before re-running a script that seeds fixed-primary-key rows — was
learned by the consequence it prevented arriving in a fully avoidable
form.

Run 2 crashed immediately when `builder()` reached
`golden_false_fails_control` again: `psycopg2.errors.UniqueViolation` on
`(ticker, date)=(GOLD_FAIL_CTRL, 2020-01-01)` — run 1's own uncleaned rows
were already sitting there. This exception, raised synchronously inside
`seed_price_bars`'s own `session.commit()` call, surfaced at the top level
as `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)` —
because it was raised inside code nested within the MCP `ClientSession`/
`stdio_client`'s own async task-group-managed context, even though the
raising code itself is plain, synchronous SQLAlchemy with no `await`
anywhere near it. `ExceptionGroup` (unlike `BaseExceptionGroup`) *does*
subclass `Exception` per PEP 654 — so the existing `except Exception`
clauses in this file were never the wrong type to catch. They were simply
never reached at all, because `case = builder()` sat outside every one of
them.

**Diagnosis, done before spending anything further:** queried the real
dev database directly and found 1,480 orphaned `PriceBar` rows for
`GOLD_FAIL_CTRL`, plus an orphaned `Charter` and `Hypothesis` (still
`status='testing'`). Cleaned up via `eval.fixtures.cleanup`, confirmed by
direct query via `verify_cleanup` — the same discipline every other live
script in this project already uses, applied here to state this
component's own bug left behind rather than state produced by a fixture
working as intended. Before spending any further API money, called
`build_golden_false_fails_control()` **alone** — zero LLM cost, pure
fixture construction — against the now-clean database, and confirmed it
builds and cleans up correctly. This proved the fixture's own logic was
never the problem; only the harness's handling of a database-level
construction failure was.

**The fix:** `case = builder()` now has its own dedicated `try/except`. A
construction failure is appended to a new
`GoldenSetReport.construction_errors: list[str]` field, and the loop
`continue`s to the next builder rather than propagating. This field is
deliberately a plain list of strings, not folded into `CaseResult`:
`CaseResult.category`/`expected_status` are `Literal`-typed against fields
that only exist on an *already-built* `GoldenCase`, and a builder that
raised before returning one has nothing valid to put there. Inventing a
placeholder value (`category="unknown"`) to force a construction failure
into that schema would weaken a type guarantee that exists specifically to
keep the normal-scoring-path schema honest — the cost of a second,
narrower reporting channel is far lower than the cost of a schema that can
silently hold a value its own type system says is impossible.

Re-ran the full six-case set a third time — output redirected to a file
from the start this time, and an explicit pre-flight database-cleanliness
check run first. **All 6/6 passed, real, live**: `golden_true_1` and
`golden_true_2` → `confirmed`; `golden_false_no_edge`,
`golden_false_fails_control`, and `golden_false_breaches_bar` →
`rejected`; `golden_caveat_thin_sample` → `inconclusive` — every one
matching Component 1's declared `expected_status` exactly, and every
`status_correct`/`fabrication_clean`/`caveats_ok` dimension `True`. The
database was confirmed fully clean afterward, by direct query.

**Cost to reverse:** the fix itself is small and not something to
reverse. The real, harder-to-reverse cost was already paid: the clean
estimate for one full run was ~$0.34 (measured on the dry run, extrapolated
across six cases). The actual total across this whole episode — the
`golden_true_1` dry run ($0.0567, measured precisely), two crashed
attempts (each got two to three cases fully through their live loop
before dying, unmeasured but roughly $0.15–0.20 each at the same per-case
rate), and the final clean run (~$0.34) — comes to roughly **$0.75–$0.85**,
about double the clean estimate. Every dollar of that overrun is
attributable to the re-run-without-checking-state mistake, not to any
single run costing more than predicted.

### `run_golden_set` owns the full lifecycle rather than accepting pre-built cases

Covered above in Section 2 for completeness: cleanup has to run right
after each case regardless of who builds it, so decoupling construction
from execution would not have changed the fix, only where the loop lives.
Worth restating here because the bug above is a direct illustration of why
that decision holds — the failure that crashed the batch happened *during*
construction, inside the very loop this design keeps unified.

### `on_usage`, additive rather than a breaking signature change

**Chosen:** an optional keyword parameter with a `None` default.

**Alternative considered:** change `structured_output`'s return type to a
tuple `(response_model, Usage)`, forcing every caller to unpack it.

**Why rejected:** this function has five real call sites across three
separate modules (`charter.py`, `hypothesis.py`, `study_design.py`) plus
the loop and `render_verdict`, none of which need usage data for their own
purposes. Forcing all of them to unpack a tuple they don't use would be a
breaking change to a function whose own docstring already states a
deliberate, considered contract ("a valid `response_model` instance, or an
exception") for reasons unrelated to this feature. An optional callback
gets the one caller that needs this (the cost-measurement dry run) exactly
what it needs, with literally zero lines changed anywhere else.

**Cost to reverse:** trivial — remove the parameter and the one `if`
check. Nothing depends on it existing except the specific measurement
script that used it once.

---

## 4. Concepts introduced

**Non-atomic multi-step construction, and why "the function either fully
succeeds or fully fails" cannot be assumed for free.** `builder()` looks
like a single call from the outside, but it performs several independent
database commits in sequence (`seed_price_bars`, then
`build_charter_and_hypothesis`, then `build_study_design`). A failure on
step two leaves step one's effects permanently persisted — there is no
transaction wrapping the whole function, and adding one would require
restructuring `eval.fixtures`' own session-per-step pattern. The general
lesson: any function that performs more than one commit is a function
whose partial-failure behavior needs to be reasoned about explicitly, not
assumed away by treating the function as a single atomic unit because it
has one name and one call site.

**`ExceptionGroup` and where async task groups actually surface an
error.** Python 3.11's `asyncio.TaskGroup` (and libraries built on
`anyio`, which MCP's stdio transport uses) can wrap an exception raised
inside their managed scope in an `ExceptionGroup`, even when the raising
code itself is plain synchronous code with no concurrency of its own. The
practical consequence for this project: an `except Exception` clause
placed *outside* the scope where an error actually originates will not
help, no matter how broad it is — the fix has to move the guard to where
the failure can occur, not widen the exception type being caught. This
generalizes past this one bug: any code running inside an `async with`
block managed by a task-group-based library inherits that library's
exception-propagation shape, whether or not the code itself ever awaits
anything.

**Estimate versus measurement, applied a second time.**
`step-08-live-execution-loop.md` names this project's own precedent
directly: `MAX_STEPS=40` was an estimate, and Component 6b then measured
the real 3.0-steps-per-window rate rather than trusting the guess. This
component repeats the same move on a different question — not "how many
steps," but "how much does a case cost" — and the two independent
estimation methods (`docs/architecture.md`'s own $0.20–0.35/study figure,
scaled by call-count fraction, versus a bottom-up token estimate from the
actual prompt shapes) disagreed by roughly 2–3x before either was
measured. The real, measured number for `golden_true_1` — 7 real Bedrock
calls (6 `AgentDecision` + 1 `ParsedVerdict`, matching Component 6b's own
observed 3.0-steps-per-window rate across two windows exactly), 10,916
input tokens, 1,598 output tokens, **$0.0567** — landed close to the
bottom-up estimate, not the architecture-anchored one. The likely reason:
`docs/architecture.md`'s $0.20–0.35 figure covers a *full* study, whose
hypothesis-generation and literature-grounding prompts carry retrieved
text and are almost certainly larger than the compact window summaries a
`decide_next_action` prompt ever sees — and golden-set cases pay for none
of that, by Component 1's own deliberate design (skipping Steps 2–4's own
LLM calls). The general point, stated the same way Component 6b's own
explainer states it: an estimate and a measurement are different kinds of
claim, and conflating them is how systems acquire numbers nobody can
defend.

---

## 5. How this component was verified

**Layer 1 — the real bug's own resolution, verified for free before
spending anything further.** `build_golden_false_fails_control()` called
alone, with no LLM and no live loop, against a database confirmed clean by
direct query beforehand. This isolated the question "is the fixture
itself broken" from "is the harness's error handling broken" — the answer
was the second one, and this step is what proved it before a fourth real
Bedrock spend was risked on a hypothesis that turned out to be wrong.

**Layer 2 — the full live run, real Bedrock, real MCP subprocess, all six
cases.** `scripts/run_golden_set.py`, output redirected to a file from the
start (a direct, disclosed correction of the mistake that made run 1
undiagnosable), preceded by an explicit database-cleanliness pre-flight
check. Result: 6/6 passed — every case's `status_correct`,
`fabrication_clean`, and `caveats_ok` all `True`, matching Component 1's
declared expectations across all three verdict categories (`confirmed`,
`rejected`, `inconclusive`). Report persisted to
`reports/golden_set/20260827T213724Z.json`. Database confirmed fully clean
afterward.

**What this does not prove.** Six passing cases on a correctly-functioning
agent is real, valuable evidence that the harness itself works — it does
not yet prove the harness *catches a regression* when the agent is broken,
which is the literal text of Stage 6's own gate ("catches a
deliberately-broken agent") and is deliberately left to
`scripts/verify_stage6_gate.py` (Component 5), not this component. Nor
does it change the reliability question `stage-5-summary.md` raised about
GATE5PROBE: two confirm cases and three-plus-one reject/caveat cases here
are more data points than that stage had, but they are still Component
1's own engineered fixtures, not a claim about how this system performs on
a real, ambiguous hypothesis nobody constructed the answer to in advance.
That question remains open by design — the golden set's own honest limit,
not a gap in this component specifically.

---

## 6. Interview defense

**"Walk me through what actually went wrong the first time you ran
this."** `run_golden_set`'s per-case loop called each fixture's builder
function directly, outside any exception handling, on the reasoning that
a builder failing meant a bug in the fixture's own logic rather than
something a batch runner needed to survive. That reasoning missed that
building a fixture also means writing to a real database in several
separate commits, and a database-level failure — here, a leftover row
from an earlier crashed run colliding on a primary key — is an
operational failure, not a logic bug, and needed the same containment
every other unexpected failure in this function already had. The fix
moved that call inside its own guard and gave construction failures their
own reporting channel, separate from a normally-scored case, because
forcing a failed build into the same typed schema as a real result would
have meant inventing a value that schema's own types say cannot exist.

**"Why didn't you just wrap `structured_output` from the outside to
measure token usage, instead of touching a Stage 5 module?"** Because a
wrapper that reimplements the request-building logic just to reach
`response.usage` would be measuring a parallel reimplementation, not the
function that actually runs in production — the same reasoning
`step-08-live-execution-loop.md`'s `TracingLLM` already used to justify
observing `decide_next_action` from outside rather than editing it
directly, applied here in the opposite direction: this case genuinely
needed the one real call site touched, non-invasively, because there was
no way to get at `response.usage` without either editing the function
that makes the request or duplicating it. The change is purely additive —
every existing caller passes nothing and sees zero behavior difference —
which is the same bar `TracingLLM` itself was held to.

**Hard question: "You spent real money re-running a broken script before
even knowing what broke it. Isn't that exactly the kind of carelessness
this project's own discipline is supposed to prevent?"** Yes, and I'd
rather say that plainly than minimize it. The mistake was concrete and
avoidable: the first run's output was truncated in a way that made
diagnosis impossible, and instead of stopping to check what state that
crash had left behind, I re-ran the entire batch again — which is exactly
the "check state before re-running something with side effects" discipline
this project already applies to git operations, applied insufficiently to
a script that seeds fixed-primary-key database rows. It cost roughly
double the clean estimate, and I named the actual dollar range rather than
rounding it away. What I'd defend is what happened *after* noticing: I
queried the database directly rather than guessing, verified the fix at
zero cost before spending anything further, and only then re-ran the full
batch — with output captured properly this time. A mistake that gets
found, root-caused, and fixed with the same rigor as everything else in
this project is a different thing from a mistake that gets hidden; I don't
think the first one undermines the project's discipline, but I also won't
pretend the money wasn't real.

**"What would you do differently?"** Redirect output to a file as the
default way any live script in this project gets run, not just the ones
where a previous failure already taught the lesson — the pattern GATE5PROBE's
own gate script already modeled (real cost, careful, deliberate,
one-shot) should have been the template from this component's very first
invocation, not adopted only after the first one went badly.

---

## 7. What comes next and why

**Component 4 — `tests/eval/test_harness.py`.** Deterministic, mocked-LLM
tests of `_score`'s own scoring logic — does it correctly mark
`status_correct=False` on a mismatch, does it detect a missing caveat
substring, does a `None` verdict correctly fail all three dimensions —
the same "test the code, not the live system, for properties that are
about code" split this component's own Section 2 already names.

**Component 5 — `scripts/verify_stage6_gate.py`.** Stage 6's actual gate:
run the golden set against the real, working agent (this component just
did that — 6/6), then deliberately break something load-bearing
(`decide_status` forced to always return `"confirmed"` is the leading
candidate) and confirm the harness's own report correctly flips the
planted-false and known-caveat cases to failing, with a clear reason
attached to each. That is the literal text of Stage 6's gate — "catches a
deliberately-broken agent" — and this component's own honest 6/6 pass is
the necessary first half of that proof, not the whole of it.

**If this component were wrong** — if `_score`'s logic quietly awarded
`fabrication_clean=True` to a case that never produced a verdict, for
instance — the failure would not surface in this component's own 6/6
result, because every one of these six cases legitimately did produce a
verdict. It would surface exactly the way Component 5's gate is built to
catch it: a deliberately broken agent producing wrong verdicts that this
harness nonetheless reports as passing. That is precisely why a clean run
against a working agent, however real and however hard-won, is still only
half the proof this stage exists to build.
