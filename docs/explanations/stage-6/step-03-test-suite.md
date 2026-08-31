# Step 3 — Component 4, the Test Suite

## 1. What this does

`tests/eval/` is the deterministic, zero-cost test suite for Stage 6's own
code — not for the agent Stage 6 evaluates. Three new files:
`tests/eval/conftest.py` (the `eval_db_session` fixture),
`tests/eval/test_harness.py` (unit tests for `eval.harness._score`), and
`tests/eval/test_golden_cases.py` (regression coverage for
`eval.golden_cases`, added beyond the original plan — see Section 3).

**What this is not.** It is not a test of whether the *agent* behaves
correctly — that is Component 2's own live run (six real cases, real
Bedrock, all passing) and Component 5's still-pending gate proof. Nothing
here calls Bedrock, launches the MCP subprocess, or costs a cent. It also
does not re-verify the real backtest numbers (Sharpe, p-value, trade
count) `step-01-golden-cases.md` already established directly against
`run_backtest`/`test_significance` — those are deterministic given fixed
seeds and were already proven with real margin; this suite's job is a
different property entirely (construction, persistence, cleanup, and pure
scoring logic), not a re-check of numbers that haven't changed.

---

## 2. Every meaningful line explained

### `eval_db_session` — two monkeypatch targets, not one

```python
monkeypatch.setattr("eval.fixtures.SessionFactory", Session)
monkeypatch.setattr("eval.golden_cases.SessionFactory", Session)
```

`SessionFactory` is imported directly into two separate modules —
`eval.fixtures` (used by `build_charter_and_hypothesis`,
`build_study_design`, `cleanup`, `verify_cleanup`) and `eval.golden_cases`
(used by its own `_seed` helper) — so patching
`data_pipeline.db.session.SessionFactory` itself would have no effect on
either name once each module has already bound its own copy at import
time. This was modeled directly on `tests/agentic_core/conftest.py`'s
existing split between `loop_db_session` (patches
`agentic_core.loop_graph`) and `corpus_db_session` (patches
`agentic_core.corpus`, separately, for the identical reason) — read and
copied *before* writing this fixture, not discovered by a failing test
after the fact. Worth stating plainly which of those two things actually
happened, since this project's own convention is to say so rather than
leave it ambiguous: this one was anticipated correctly.

```python
conn.execute(text(
    "TRUNCATE tool_call_traces, study_runs, study_designs, hypotheses, charters, "
    "price_bars CASCADE"
))
```

`price_bars` is new in this TRUNCATE list — `loop_db_session`'s own
version never needed it, because nothing in `tests/agentic_core/` writes
`PriceBar` rows. Golden-set fixtures are the first thing in this project's
test suite to build a `Charter`/`Hypothesis` pair *and* seed real price
data for it in the same fixture.

### `test_harness.py` — `_score` only, deliberately

```python
def test_no_verdict_from_exhausted_retries_scores_identically_to_a_loop_failure():
    case = _fake_case(expected_status="confirmed")
    result = _score(case, "run-2", None, "verdict validation failed after retries: [...]")
    assert not result.fabrication_clean
```

This test exercises the *scoring consequence* of `render_verdict` raising
`VerdictValidationError`, without ever calling `render_verdict` or faking
an MCP session to get there. The reason that's sufficient, not a
shortcut: `run_case`'s job when the loop never reaches `'completed'` and
its job when `render_verdict` exhausts every retry attempt both end at
the exact same place from `_score`'s point of view — `verdict=None`. A
test that actually drove `run_case`'s try/except through a faked session
and a faked database would be testing the same input-to-output mapping
`_score`'s own unit tests already cover, just through far more expensive
scaffolding to set up.

`_fake_case`/`_fake_verdict` build schema-valid but content-arbitrary
`GoldenCase`/`Verdict` objects — a fake `StrategyRule`, fake dates, fake
narrative text. This is safe specifically because `_score` never reads
`case.charter`/`case.hypothesis`/`case.design`/`case.ticker` — only
`case.name`, `case.category`, `case.expected_status`, and
`case.expected_caveat_substring`. Building fully realistic fixture content
here would be effort spent on properties this function provably never
inspects.

### `test_golden_cases.py` — construction and persistence, not correctness

```python
def test_ticker_names_fit_the_real_price_bar_column(eval_db_session):
    for builder in GOLDEN_CASE_BUILDERS:
        case = builder()
        assert len(case.ticker) <= 16, ...
```

A direct, named regression test for the exact bug `step-01-golden-cases.md`
records: four of the six original ticker names exceeded
`PriceBar.ticker`'s `String(16)` limit and failed on insert with
`StringDataRightTruncation`. Asserting the length directly, rather than
only asserting that a real insert succeeds, means a future case that
reintroduces an overlong ticker fails *this specific, fast, free test*
instead of a real paid database write during a live run.

```python
def test_each_case_has_a_distinct_ticker(eval_db_session):
    ...
    assert len(tickers) == len(set(tickers))
```

Motivated by how `run_golden_set` actually operates: all six cases are
built, run, and only *then* cleaned up, one at a time, inside the same
loop — meaning at any given moment during a real run, more than one
case's `PriceBar` rows can exist in the database simultaneously. A
ticker collision between two cases would make their price data
indistinguishable to the backtester, silently corrupting whichever case
ran second.

### What was skipped

Genuine boilerplate: `test_every_builder_produces_a_schema_valid_case`'s
own straightforward attribute assertions, and the plain `try/finally`
cleanup wrapping every test that calls a builder (identical in shape
across all four `test_golden_cases.py` tests).

---

## 3. Design decisions and rejected alternatives

### `_score` gets thorough unit tests; `run_case` gets none

**Chosen:** the entire unit-test burden sits on `_score`.

**Alternative considered:** fake the MCP `ToolSession` (the same
`Protocol` Component 6a's own tests already fake for `loop_graph`) and
monkeypatch `agentic_core.verdict.SessionFactory` to drive `run_case`'s
actual control flow through a scripted scenario, end to end.

**Why rejected:** `agentic_core.verdict.render_verdict` has never been
unit-tested with fakes anywhere in this project — not even in Stage 5's
own `tests/agentic_core/test_verdict.py`, which tests `decide_status` and
`validate_claims` (the pure functions `render_verdict` calls) but never
`render_verdict` itself, precisely because of the real, multi-table
database I/O involved (it reads `Charter`, `Hypothesis`, `StudyDesign`,
`StudyRun`, and `ToolCallTrace` rows and writes a `Verdict`). Building
that fixture machinery now, one level up, just to fake `run_case`'s call
into it, would be a new pattern this project has consistently chosen not
to build — not filling a gap, but reversing an established decision
without a new reason to. The reason there's no new reason: both
control-flow branches `run_case` needs to handle collapse to the same
`verdict=None` input `_score` already exercises directly.

**Cost to reverse:** moderate — if `run_case`'s own orchestration logic
ever grows a third branch that *doesn't* reduce cleanly to a `_score`
input (for instance, a retry-and-resume path), this decision would need
revisiting, and building the fake-session scaffolding at that point would
be real, new work, not a small addition.

### `test_golden_cases.py`: construction and cleanup, not backtest correctness

**Chosen:** this file checks that every builder produces a valid,
persistable, fully-cleanable `GoldenCase` — nothing about whether the
Sharpe ratios or p-values it will eventually produce are correct.

**Alternative considered:** re-run each fixture's series through
`run_backtest`/`test_significance` here too, asserting the same numbers
`step-01-golden-cases.md` already recorded.

**Why rejected:** those numbers are fully deterministic given the fixed
seeds each builder uses, and they were already verified directly, with
stated margin, against the real functions — repeating that check here
would be re-doing Component 1's own verification, not adding new
coverage. What *hadn't* been checked anywhere permanent, before this
file, was whether a builder's real database writes persist and tear down
correctly — a category of bug (the ticker-length limit, the
leftover-row collision) that has nothing to do with whether the computed
numbers are right, and everything to do with whether the fixture can
exist safely in a shared database at all.

**Cost to reverse:** none — this is purely additive test coverage with no
constraint on how Component 1's fixtures are built.

### Promoting the scratch smoke test into a permanent suite

**Chosen:** `test_golden_cases.py`, run automatically on every test
invocation.

**Alternative considered:** leave Component 1's own
`smoke_test_golden_cases.py` as a manual, one-off script, the way it was
originally used.

**Why rejected:** that script is exactly what caught the ticker-length
bug — once, manually, during Component 1's own development. It was never
run again, and it would not have run automatically before the live crash
Component 2's own explainer records. A check that exists but only runs
when someone remembers to invoke it by hand is not the same guarantee as
one that runs in CI on every change. This was proposed, not part of the
original top-level plan, specifically because Component 2's real bug
demonstrated the gap directly rather than hypothetically.

**Cost to reverse:** trivial to remove, and there is no reason to.

---

## 4. Concepts introduced

**Testing a system's failure modes by their *consequence*, not their
*mechanism*.** `_score`'s tests never construct the actual exception
(`VerdictValidationError`) or the actual failed-loop state `run_case`
would produce — they construct the *result* those paths converge on
(`verdict=None`) and test what happens from there. This is a narrower,
cheaper claim than "the orchestration code correctly catches this
exception" (which is not tested here at all), but it is exactly the claim
that matters for `_score`'s own correctness, and conflating the two would
mean paying for expensive test infrastructure to verify a property a
much simpler test already covers.

**A regression test can encode a specific historical bug, not just a
general property.** `test_ticker_names_fit_the_real_price_bar_column`
doesn't just check "the ticker is a reasonable length" in the abstract —
it checks the exact numeric limit (`16`) that a real, named, previously-
encountered failure violated. Tying a test directly to a bug's own root
cause, rather than to a vaguer general principle, is what makes the test
catch a *reintroduction* of that specific bug rather than merely feeling
thorough.

---

## 5. Verification

`pytest tests/eval/` — 16 tests, all passing, in under nine seconds, zero
API cost. Then the full project suite — `pytest tests/` — 328 tests,
all passing, specifically to confirm the one production-code change this
stage has made so far (`llm_client.structured_output`'s new `on_usage`
parameter, Component 2) introduced no regression anywhere else that
function is called from (`agentic_core/charter.py`, `hypothesis.py`,
`study_design.py`, `loop_graph.py`, `verdict.py`).

**What this does not prove.** This suite proves `_score`'s scoring logic
is correct and that all six fixtures construct and tear down cleanly
against a database. It does not prove `run_case`'s live orchestration is
correct — that claim rests entirely on Component 2's own real, live 6/6
run, which is a different kind of evidence (one observation of the real
system working) than a mutation-tested unit suite (many observations of
a pure function's boundary behavior). Both kinds of evidence exist for
this stage; neither substitutes for the other.

---

## 6. Interview defense

**"Why does `_score` get sixteen tests and `run_case` gets zero?"**
Because `render_verdict` — the function `run_case`'s own orchestration
logic wraps — has never been unit-tested with fakes anywhere in this
project, including in Stage 5 itself, and for the same reason: real,
multi-table database I/O that this project has consistently chosen to
verify live rather than through fixture machinery. The two control-flow
branches `run_case` needs to handle both reduce to the exact same input
`_score` already tests directly — `verdict=None` — so there's no
scoring behavior left untested, only orchestration mechanics that this
project's own precedent already treats as a live-only concern.

**"Why didn't you just fake the MCP session and test the whole loop end
to end here?"** I could have, and it's the more thorough-looking answer.
But it would mean building real fixture infrastructure — a fake
`ToolSession`, a monkeypatched `agentic_core.verdict.SessionFactory`, a
scripted sequence of tool responses — to prove something the six-case
live run in Component 2 already proved with stronger evidence: a real
agent, making its own real decisions, correctly reaching the right
verdict. A fake session proves the code *can* be driven to a state;
Component 2's live run proves the *real* system actually reaches it.

**Hard question: "`test_golden_cases.py` checks that fixtures build and
clean up — but doesn't verify their actual backtest numbers. Isn't that
a real gap, months from now, if someone changes a fixture's parameters?"**
It's a real, bounded gap, and worth stating exactly where the boundary
is. If someone edits `golden_true_1`'s dip percentage in six months, this
suite will confirm the edited fixture still builds and cleans up — but it
will not catch that the edit silently invalidated the fixture's own
verified margin (say, if the new parameters made the real Sharpe drop
below 0.5). The mitigation isn't in this file — it's the discipline
`step-01-golden-cases.md` establishes for changing a fixture at all:
re-verify the real numbers directly against `run_backtest`/
`test_significance` before trusting a changed fixture, the same way that
document did the first time. This suite protects against a different,
real failure (a broken build or a database collision), not against a
parameter edit invalidating a fixture's own math — and I'd rather name
that boundary than imply this suite covers more than it does.

---

## 7. What comes next and why

**Component 5 — `scripts/verify_stage6_gate.py`.** Stage 6's actual gate:
run the golden set against the real, working agent (already done, live,
6/6, in Component 2), then deliberately break `decide_status` and confirm
this harness's own report correctly flips the planted-false and
known-caveat cases to failing. This test suite's job in that story is
narrow but real — it's what lets Component 5 trust that a flipped result
in the gate script reflects a real change in the *agent's* behavior, not
a bug in how the harness scores what it observes.

**If this component were wrong** — if `_score` had a bug that, say,
marked `caveats_ok=True` even when a required substring were absent —
the failure would not be visible in any of this stage's own live runs,
because none of the real fixtures currently exercise that exact edge
incorrectly. It would surface exactly the way Component 5's gate is
designed to catch a different class of problem: a silently-wrong scoring
rule producing a report that looks clean when it shouldn't. That is why
this suite exists as a genuinely separate check from the live run, not a
formality alongside it.
