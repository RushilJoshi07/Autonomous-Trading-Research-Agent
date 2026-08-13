# Step 8 — Completing the Formal Test Suite (Stage 3, plan §8)

## 1. What this does

Every prior Stage 3 component was built, then verified informally or
interactively, with the intention of writing formal `pytest` coverage later —
`evaluator.py` (Component 4) has never had a dedicated test file at all;
`rule_strategy.py` (Component 5) has exactly one test, a hand-written
regression case for a real bug found while building it; `test_extended_indicators.py`
(Component 8) shipped deliberately minimal, just the adversarial
rejects/accepts pair. This component is the dedicated pass that closes all
three gaps: `tests/backtester/test_evaluator.py` (new, 28 tests),
`tests/backtester/test_rule_strategy.py` (6 tests added), and
`tests/backtester/test_extended_indicators.py` (3 tests added). The suite goes
from 133 tests to 170, all passing.

This is not new product functionality — no line of `src/` changed. What
exists now that didn't before is *proof*, in a form that survives future
changes automatically: a regression in any of `evaluator.py`'s NaN handling,
`rule_strategy.py`'s dedup logic, or the extended registry's "unverified means
unusable" contract now fails a specific, named test instead of surfacing
later as a wrong backtest result nobody traced back to its cause.

What this is *not*: it doesn't touch the Stage 3 gate script (plan §9, still
outstanding) or Alembic (plan §10, still outstanding). Closing this component
is what unblocks both — the gate script's job is to prove the whole compiled
pipeline produces literature-consistent numbers on *real* AAPL data, and doing
that meaningfully depends on the pipeline's individual pieces already being
proven correct in isolation, which is what this component is for.

---

## 2. Every meaningful line explained

### `test_evaluator.py`'s `FakeBarContext`

```python
class FakeBarContext:
    def __init__(self, prices: dict[tuple[str, int], float] | None = None):
        self._prices = prices or {}

    def price(self, field: str, offset: int) -> float:
        return self._prices[(field, offset)]

    def indicator(self, key, offset: int) -> float:
        raise NotImplementedError("not needed for these tests")
```

`evaluator.py`'s own module docstring states its whole reason for existing:
isolated testing, independent of `backtesting.py`. Taking that seriously means
these tests should never construct a real `Strategy`, real price data, or a
real indicator — they should hand `resolve_term`/`evaluate_comparison`/
`evaluate_condition` a `BarContext` implementation whose every value is
explicit and under the test's control. `FakeBarContext` is that
implementation: a plain dict keyed `(field, offset)`, nothing clever.

The dict lookup is deliberately **not** `.get()` with a default — it's a bare
`self._prices[(field, offset)]`, which raises `KeyError` on anything not
explicitly set up. This is a real design choice, not laziness: a silently
defaulting fake context would let a test accidentally pass by reading an
implicit zero or `None` instead of the value the test actually meant to
supply, which is exactly the kind of quiet-wrong-answer failure mode this
whole codebase's discipline exists to avoid. If a test needs offset `-1`'s
`"high"` value and forgets to supply it, it should crash loudly during setup,
not silently evaluate against a phantom `0.0`.

`indicator()` raises `NotImplementedError` rather than being backed by a
second dict. None of the plan §8 evaluator cases exercise `IndicatorTerm`
resolution directly — `resolve_term`'s `IndicatorTerm` branch just calls
`ctx.indicator(indicator_key(term), term.offset)`, a single delegating line
already proven correct by construction (nothing about ITS correctness depends
on evaluator.py; it depends on whatever backs the real `BarContext`, which is
`rule_strategy.py`'s job and `rule_strategy.py`'s tests' job to prove). Adding
a second fake-data dict here for a case nothing actually calls would be
untested surface area for no reason — the `NotImplementedError` makes that
boundary explicit rather than silently supporting a code path with zero
coverage behind it.

### Testing crossover as a genuine flip, not a threshold

```python
def test_crosses_above_true_on_real_flip():
    ctx = FakeBarContext({("high", -1): 5.0, ("low", -1): 10.0, ("high", 0): 15.0, ("low", 0): 10.0})
    ...

def test_crosses_above_false_when_already_above_both_bars():
    ctx = FakeBarContext({("high", -1): 20.0, ("low", -1): 10.0, ("high", 0): 25.0, ("low", 0): 10.0})
    ...
```

The second test is the one that actually matters here. `evaluator.py`'s
`_evaluate_crossover` correctly checks `left_prev < right_prev and left_now >
right_now` — a genuine transition, not just "is left currently greater." A
weaker implementation checking only `left_now > right_now` would pass the
first test too (left ends up above right in both cases) and would be
indistinguishable from the correct one *unless* a test specifically
constructs a case where left is above right on both bars and confirms the
comparison does **not** fire. Without this second test, a regression that
silently degrades `crosses_above` into a threshold check (firing on every bar
left stays above right, not just the bar it crosses) would pass every other
test in the suite.

### Positive offset, via the mutation bypass — twice, at two different layers

```python
term = PriceTerm(field="close", offset=-1)
term.offset = 1  # bypasses schema.py's construction-time validator
with pytest.raises(ValueError, match="lookahead"):
    resolve_term(term, FakeBarContext())
```

and, in `test_rule_strategy.py`:

```python
rule.entry.comparison.left.offset = 1  # bypasses schema.py's construction-time validator
strategy_cls = make_rule_strategy(rule)
with pytest.raises(ValueError, match="lookahead"):
    run_backtest(synthetic_data, strategy_cls, ticker="SYNTHETIC")
```

`schema.py`'s `_OffsetTerm._check_offset` validator already refuses to
*construct* a term with `offset > 0` — proven in `test_schema.py`'s
`test_positive_offset_rejected`. If that were the only test of this property
anywhere in the suite, it would leave a real gap: `evaluator.py`'s own,
independent `validate_offset` call inside `resolve_term` (and `_shifted`, for
crossover's implicit "one bar deeper" reach) would be completely untested —
nothing would notice if someone removed it, reasoning "schema.py already
checks this at construction time, so it's redundant here." It is not
redundant: these Pydantic models aren't frozen or `validate_assignment`, so
`term.offset = 1` after construction is legal Python and completely invisible
to schema.py's validator, which only ever runs once, at construction. This
is not a hypothetical concern — it's the exact mechanism Component 4's commit
log already documented as the reason `resolve_term` needed its own check in
the first place, and this component adds the test that actually exercises it.

Two tests use this technique, at two different layers, on purpose. The
`test_evaluator.py` version proves `resolve_term` itself catches it, in
isolation. The `test_rule_strategy.py` version proves the **whole compiled
pipeline** still catches it — `make_rule_strategy` compiles the rule, and a
real `run_backtest` call executes it bar by bar — because it's possible in
principle for a compilation step to construct new, unmutated copies of terms
internally (defeating a mutation applied before compilation) or for
`next()`'s call path to somehow route around `evaluate_condition`. Neither
turned out to be true — the pipeline test passes for the right reason, and
section 5 describes exactly how that was confirmed rather than assumed — but
proving it at only one layer would have left the other one asserted, not
demonstrated.

### `test_extended_indicators.py`'s stub-validity test imports the real file

```python
from backtester.extended_indicators import EXTENDED_INDICATORS
...
def test_generated_stubs_are_structurally_valid():
    assert len(EXTENDED_INDICATORS) > 0, "expected the real generated file to contain candidates"
    for name, spec in EXTENDED_INDICATORS.items():
        ...
```

This imports the actual, checked-in `extended_indicators.py` — the same
module `registry.py` itself imports — not a hand-built sample dict. The user
asked directly, before approving this plan, whether this test runs against
the real file or a synthetic fixture, and the answer is load-bearing to the
test's actual purpose: this check exists to catch a **future** bad
regeneration (a code change to `generate_extended_indicators.py` that starts
silently producing malformed entries — an empty `inputs` tuple, an invalid
field name, a `min >= max` bound). A synthetic fixture, built once and frozen,
would never see that regeneration happen and would keep passing regardless of
what the real generator started producing. Testing the actual artifact means
this check re-validates automatically every time someone reruns generation,
with zero separate fixture to remember to keep in sync — the same reasoning
`test_schema.py`'s extended-indicator smoke test already applies by
referencing `AROOND` from the live registry instead of a hand-built spec.

---

## 3. Design decisions and rejected alternatives

**Morning star's KNOWN_STRATEGIES test doesn't require trades.** The other
three strategies (SMA crossover, RSI 14, RSI 2) assert `result.num_trades > 0`
on the shared 500-bar synthetic fixture. Morning star is a rare, specific
three-bar candlestick pattern; requiring it to fire at least once on one fixed
random seed's worth of synthetic data would make the test's pass/fail status
a function of that seed's particular noise, not of whether the strategy is
correctly wired. The alternative — asserting `num_trades > 0` for morning
star too — was rejected because a test that can fail from bad luck on
unrelated data, rather than from an actual code defect, teaches the wrong
lesson every time it flakes: "rerun it" instead of "something's wrong." The
plan's own §8 language ("few trades fine; must execute") already anticipated
this; the test asserts exactly that: it compiles, it runs to completion, and
it doesn't raise — which is precisely what would break if morning star's
7-leaf `and` condition were ever malformed, without being coupled to whether
this particular random walk happens to contain one.

**The dedup-count test is separate from the existing liveness test, not
merged into it.** The pre-existing `test_deduplicated_indicator_advances_per_bar_not_static`
already proves two references to one indicator collapse to one *live,
correctly-updating* attribute — a strong, expensive-to-set-up test (it
instruments a running backtest and records every bar's value). The new
`test_sma_crossover_dedups_to_two_unique_indicators` proves a narrower,
cheaper fact — the *count* comes out right — on a real `KNOWN_STRATEGIES`
rule rather than a hand-built one. These deliberately don't overlap in what
they prove: the existing test could pass even if dedup accidentally created 2
attributes instead of 1 in some other, untested rule shape (it only checks
one specific case in depth); the new test could pass even if the *live*
liveness property were broken (it never runs a backtest, just counts keys).
Together they cover more ground than either extended to also do the other's
job — merging them would mean the "cheap structural check on a realistic
multi-indicator rule" property stops being verified on its own, independent
of the expensive instrumented case.

**The unverified-indicator test in `test_extended_indicators.py` deliberately
overlaps with `test_schema.py`'s existing case.** `test_schema.py`'s
`test_unverified_indicator_rejected` already picks `next(name for name, spec
in ALL_INDICATORS.items() if not spec.verified)` and confirms it raises — and
since every core entry is `verified=True` by construction, that test already
only ever exercises an extended-tier rejection in practice. Writing a nearly
identical test again here, scoped explicitly to `EXTENDED_INDICATORS` rather
than the merged `ALL_INDICATORS`, was a deliberate choice to keep
`test_extended_indicators.py` self-contained as "here is the extended tier's
whole contract, provable by reading this one file" rather than requiring a
reader to know that `test_schema.py`'s generic-sounding test happens to be
the extended-tier test in disguise. The cost is a small amount of literal
duplication; the alternative (deleting one of the two) would have made
`test_extended_indicators.py` incomplete on its own terms, contradicting the
plan's own explicit scoping of what belongs in this file.

---

## 4. Concepts introduced

**Test-double strictness as a design choice, not an accident.** Covered in
section 2 for `FakeBarContext` specifically — the general principle is that a
test double's *failure mode when misused* is itself a decision worth making
deliberately. A permissive fake (returns a sensible default for anything not
explicitly configured) makes tests shorter to write and hides setup mistakes;
a strict fake (raises on anything not explicitly configured) makes tests
slightly more verbose but turns "I forgot to set up this value" into an
immediate, loud `KeyError` at the exact line that needed it, rather than a
quiet wrong number three assertions later.

**Proving a test isn't vacuous by reintroducing the bug it claims to catch.**
Not a new concept in this project — Component 5's dedup regression test
needed a second pass specifically because its first version passed even with
the bug it was meant to catch, deliberately reintroduced — but this component
is the first time that discipline was applied *proactively*, before shipping,
rather than discovered after the fact. Section 5 walks the actual mechanism:
monkeypatching a shared validator function across three modules' already-bound
references (not just the one module that originally defined it), which is
itself worth understanding on its own terms — see below.

**Why patching one module's function doesn't patch another module's copy of
it.** `schema.py` and `evaluator.py` both do `from .indicators import
validate_offset`. This binds the *name* `validate_offset` inside `schema.py`'s
and `evaluator.py`'s own module namespaces to whatever function object
`indicators.validate_offset` pointed to *at import time*. Reassigning
`indicators.validate_offset` afterward changes what `indicators.py` itself
would call `validate_offset`, but does nothing to `schema.validate_offset` or
`evaluator.validate_offset` — those names already point at the original
function object and were never told to look anywhere else. This is why the
bug-reintroduction check in section 5 patches all three modules' bound names
explicitly, not just `indicators.py`'s: patching only the source module would
have silently failed to actually disable the checks the tests were supposed
to be exercising, producing a false confidence that the check was proven
necessary when it hadn't been tested at all.

---

## 5. How this component was verified

Every new test was run and passed on the first complete run of each file
(`test_evaluator.py`: 28/28; `test_rule_strategy.py`: 7/7 including the
preserved test; `test_extended_indicators.py`: 5/5 including the preserved
pair), then the full suite: 170/170.

That alone proves the tests pass. It does not prove they'd *fail* if the
thing they're supposed to protect were broken — a test that asserts something
trivially true passes for the wrong reason and provides zero actual coverage.
So, specifically for the two positive-offset tests (the ones with the most
subtle failure mode, since a mistake in either could silently pass by
accident — e.g., if `FakeBarContext`'s `KeyError` on a missing price happened
to look like the expected failure), `validate_offset` was monkeypatched in an
isolated, throwaway Python subprocess to remove its `offset > 0` check
(keeping only the `offset < -MAX_LOOKBACK` check), across all three modules'
bound references (see section 4). Both tests were re-run against this
deliberately broken code in isolation. Both failed — the evaluator-level test
with an unexpected `KeyError` (proving `pytest.raises(ValueError,
match="lookahead")` does not accidentally swallow an unrelated failure; the
test genuinely distinguishes "raised the right error" from "raised any
error") and the pipeline-level test with pytest's own "DID NOT RAISE" (proving
the guard, not some incidental side effect of `FakeBarContext` or the
backtest fixture, was what made the original version pass). The real,
unpatched suite was then re-run to confirm 170/170 with the check restored.

**What this does not prove.** These tests exercise the pure logic layers
(`evaluator.py`, `rule_strategy.py`'s compilation, the extended-tier
registry's contract) against synthetic data and one fixed random seed. None
of them run against real market data, and none of them are the Stage 3 gate
itself — literature-consistent trade counts and Sharpe ratios on real AAPL
history are still entirely unverified until plan §9's gate script runs. This
component proves the pipeline's pieces behave correctly given correct inputs;
it says nothing about whether the strategies this pipeline runs are
economically sound, which was never its job.

---

## 6. Interview defense

**Q: Why build a fake `BarContext` instead of just running these through a
real backtest with hand-crafted price data?**

A: Because constructing exact NaN placement, exact crossover flips, and exact
values at exact offsets through a *real* indicator computed over *real*
OHLCV data is fragile and indirect — you'd be reverse-engineering price
series that happen to produce the value you want at the bar you want, which
is slow to write and brittle to any future change in how an indicator warms
up. A fake context lets a test state its intent directly: "at offset -1, high
was 5 and low was 10." `evaluator.py`'s own module docstring says it exists
specifically to be tested in isolation from `backtesting.py`; this suite
takes that design decision at its word.

**Q (hard): You wrote 28 evaluator tests and 6 rule_strategy tests, but only
tested the crossover "already above, no flip" false-negative case for
`crosses_above` — not `crosses_below`. Doesn't that leave a symmetric gap?**

A: It's a real, honest gap, not a deliberate scope decision — `crosses_below`
gets a true-flip test but not the "already below both bars, should not fire"
counterpart the way `crosses_above` does. The two branches share the same
four-value NaN guard (tested, and parametrized across all four positions) and
nearly identical logic (`_evaluate_crossover` is one function handling both
via a single `if cmp.op == "crosses_above"` branch), so a bug specific to
`crosses_below`'s branch that this suite would miss is unlikely but not
provably ruled out. The honest answer under questioning is to name this
directly as the asymmetry it is, not to claim symmetric coverage that isn't
actually there — and it's a five-minute fix to add if it matters before the
gate script runs against real data.

**Q: Why not just add these tests as part of Components 4, 5, and 8
themselves, instead of a separate deferred pass?**

A: Two different reasons for two different files. `test_evaluator.py` was
genuinely just deferred — Component 4 was verified informally (traced,
documented bugs found and fixed) but never got a formal suite, and nothing
about waiting made the eventual tests any different from what they'd have
looked like written immediately after. `test_extended_indicators.py` was a
deliberate, reasoned scope decision made explicitly during Component 8's own
plan review: ship the minimal adversarial pair (the new code's own core
correctness claim) alongside the component that needed it, and defer the
broader structural cases to a dedicated pass rather than let test-writing
balloon Component 8's own scope. Both leave the same honest state in between:
real, working code with informal-only verification for a while, which is
disclosed explicitly in both this document and Component 8's own step
explainer, not hidden.

**Honest weakness:** beyond the `crosses_below` asymmetry above, this
component's tests all run on one fixed synthetic dataset (`synthetic_data`,
500 bars, one seed). Nothing here tests behavior across multiple market
regimes, different volatility profiles, or actual historical data — that's
squarely plan §9's job, not this one's, but it means "170 tests passing"
should be read as "the pipeline's logic is correct," not yet as "the
pipeline produces trustworthy results on real markets."

---

## 7. What comes next and why

Plan §9 — the Stage 3 gate script (`scripts/verify_stage3_gate.py`) — is now
unblocked: it runs all four `KNOWN_STRATEGIES` against real AAPL data
(2015–2024) and checks trade counts and Sharpe ratios against
literature-consistent ranges. This component is a prerequisite in substance,
not just in plan ordering: running an unverified pipeline against real data
and getting a plausible-looking number back would prove nothing, since a
subtly wrong evaluator or a broken dedup could easily produce a number that
*looks* reasonable by coincidence. Now, if the gate script produces an
implausible result, the individual pieces it's built from are already known
correct in isolation — narrowing the search for what's actually wrong to the
integration itself (real data quirks, warmup behavior on genuine multi-year
history, the four strategies' literature-consistency ranges) rather than
having to re-suspect `evaluator.py`'s NaN handling or `rule_strategy.py`'s
dedup logic from scratch.

Plan §10 (the Alembic baseline) was explicitly deferred to stage close in the
original plan, and stage close is what plan §9 passing actually is — so it
remains correctly pending, not overdue, until the gate script's result is in.

If this component's tests were subtly wrong — passing when they shouldn't —
the most likely place it would surface is exactly there: the gate script
producing numbers outside the literature-consistent bounds on real data, with
no obvious cause in the gate script itself, would be the first sign to come
back and re-interrogate whether this suite's coverage has a gap section 6
didn't anticipate.
