# Step 3 — Pure Condition Evaluator (Stage 3)

## 1. What this stage does

`src/backtester/evaluator.py` is what turns a validated-but-inert `StrategyRule`
(from [step-02-schema.md](step-02-schema.md)) into actual answers. Three pure
functions — `resolve_term`, `evaluate_comparison`, `evaluate_condition` — take a
`Term`/`Comparison`/`Condition` object plus something that can answer "what was
this price or indicator at this offset" (a `BarContext`) and compute a real
number or a real `True`/`False`.

This is deliberately kept separate from `strategies/rule_strategy.py` (Component
5, not yet built — the thing that will eventually wire a validated rule into a
real `backtesting.py` `Strategy` subclass). The reason is testability: crossover
logic and NaN handling can be proven correct against a trivial hand-built fake
data source, with no `Backtest` run, no `self.I()`, no real price data anywhere
in the loop. What this file is *not*: it does not know about `backtesting.py`,
does not know about pandas, does not persist anything, and does not know whether
the `Condition` it's been handed came from a rule's `entry` or its `exit` — that
distinction, as the verification section below proves concretely, doesn't exist
anywhere in this file.

A small preceding change is also covered here: before writing this file, I moved
one function out of the already-committed `schema.py` and into `indicators.py`,
so this new file and the old one could share it instead of duplicating it.

---

## 2. The preceding touch-up: `validate_offset` moves to `indicators.py`

`schema.py` (Component 3) had a private `_validate_offset` function checking
`-MAX_LOOKBACK <= offset <= 0`. This new file needs the exact same check —
re-validating offsets is central to what `evaluator.py` does, as section 3 below
covers in depth. Rather than have two copies of the same bound logic drift apart
over time, I moved it to `indicators.py`, renamed to public `validate_offset`,
living immediately next to `MAX_LOOKBACK` — since "how far back can anything
legally reach" is a property of the lookback system as a whole, not specifically
of the schema layer. `schema.py` now imports it instead of defining its own copy.

**This was verified as a pure relocation, not just assumed to be one.** Before
making the change, I captured the full output of the same interactive smoke test
used to verify `schema.py` originally: all four `KNOWN_STRATEGIES` constructing,
all thirteen planned `ValidationError` cases from the Stage 3 plan, and the two
should-succeed edge cases (partial indicator params, an incomplete cross-check).
After the move, I re-ran the identical test and `diff`'d the two outputs —
empty diff, byte-for-byte identical — and re-ran the full 16-test regression
suite, which stayed green. When asked to confirm this against `test_schema.py`
specifically, I checked first and corrected the premise: no such file exists yet
(`tests/backtester/` only has `test_data_loader.py`, `test_engine.py`, and
`test_sacred_gate.py` — `test_schema.py` is Stage 3 Component 8, not yet built).
The verification above is interactive smoke-testing standing in for that file
until it exists, not a substitute that pretends to be equivalent to it.

---

## 3. Every meaningful line explained

### `BarContext` as a `Protocol`

```python
class BarContext(Protocol):
    def price(self, field: str, offset: int) -> float: ...
    def indicator(self, key: IndicatorKey, offset: int) -> float: ...
```

`typing.Protocol` gives structural typing: anything with matching `price` and
`indicator` methods satisfies this, with no inheritance required. The
alternative — an `abc.ABC` with abstract methods — would work too, but would
force every implementation, including throwaway test doubles, to explicitly
subclass it. The test double built for this component's verification (see
section 5) never imports or subclasses anything from this file; it just happens
to have the right two method signatures. The same will be true in reverse for
Component 5's real implementation, which will be backed by `backtesting.py`'s
`self.data` and `self.I()` arrays — it only needs to structurally match these two
methods, with zero import coupling back to `evaluator.py`.

### `IndicatorKey` and `indicator_key`

```python
IndicatorKey = tuple[str, frozenset[tuple[str, float]]]

def indicator_key(term: IndicatorTerm) -> IndicatorKey:
    return (term.name, frozenset(term.params.items()))
```

This is exactly the dedup key format the Stage 3 plan specifies for Component
5's indicator precomputation: `(name, frozenset(params.items()))`. A `frozenset`
of the params dict's items, rather than the dict itself, because dicts aren't
hashable and this key needs to work as a lookup key in `BarContext.indicator`'s
underlying storage — and `frozenset` is order-independent, so `{"length": 14}`
and any re-ordering of the same params always produces the same key. Defining
this function once, here, matters beyond this file: Component 5 will precompute
indicators once per unique `(name, params)` pair and needs to build the exact
same key when it does — if the two sides ever computed this key differently
(say, a plain `tuple(term.params.items())` instead of a `frozenset`, which would
be order-*dependent*), a rule using `RSI(14)` in both its entry and exit could
silently fail to deduplicate, or worse, silently look up the wrong series. One
function, imported by both sides, makes that class of bug structurally
unreachable rather than merely unlikely.

### `resolve_term`'s dispatch

```python
def resolve_term(term: Term, ctx: BarContext) -> float:
    match term:
        case ConstantTerm():
            return term.value
        case ScaledTerm():
            return resolve_term(term.term, ctx) * term.factor
        case PriceTerm():
            validate_offset(term.offset)
            return ctx.price(term.field, term.offset)
        case IndicatorTerm():
            validate_offset(term.offset)
            return ctx.indicator(indicator_key(term), term.offset)
        case BodyTerm():
            validate_offset(term.offset)
            return abs(ctx.price("open", term.offset) - ctx.price("close", term.offset))
        case MidpointTerm():
            validate_offset(term.offset)
            return (ctx.price("open", term.offset) + ctx.price("close", term.offset)) / 2
        case RangeTerm():
            validate_offset(term.offset)
            return ctx.price("high", term.offset) - ctx.price("low", term.offset)
        case _:
            raise TypeError(f"unknown term kind: {term!r}")
```

`match`/`case` with a bare class pattern (`case PriceTerm():`) is structural
`isinstance` dispatch under the hood — no `__match_args__` needed for an
empty-parenthesis pattern. This mirrors how the seven term kinds are already a
tagged union in `schema.py`, so the dispatch code reads the same way the type
itself is structured. `ConstantTerm` returns its value with no offset check at
all, because a constant has no time dimension. `ScaledTerm` recurses into its
wrapped term and multiplies — the offset check for a `ScaledTerm` happens
implicitly, inside that recursive call, on whatever term it wraps; `ScaledTerm`
itself never touches `.offset` because it doesn't have one.

The other five branches each call `validate_offset(term.offset)` as their own
first line before reading through `ctx`. That is five nearly-identical lines
rather than one shared wrapper. I chose the repetition deliberately: a version
that tried to factor this into one check before a nested `match` would either
need two levels of `match`/`case` (harder to scan) or a helper that receives the
term and figures out whether it has an offset at all (adding indirection for a
one-line check). Five short, obviously-identical lines, each sitting right next
to the read it protects, was more legible than either alternative — consistent
with this project's preference for "three similar lines over a premature
abstraction."

**Why this re-validates something `schema.py` already checked — and why that
isn't just ceremony.** Every term reaching `resolve_term` was, in principle,
already validated at construction: `schema.py`'s `field_validator` on `offset`
ran once, when the object was built. But these Pydantic models are not
`frozen=True` and don't have `validate_assignment` turned on, which means
`term.offset = 99` after construction succeeds silently — it does not re-run the
validator. This was proven directly, not just reasoned about: a valid
`PriceTerm` was constructed, its `.offset` was mutated to `99` directly on the
live object, and `resolve_term` still raised `ValueError`. Without the check
here, that mutated term would have silently read ninety-nine bars into the
future. This is a genuine, if narrow, gap in what `schema.py`'s guarantee
actually covers — not a hypothetical worry restated as fact.

### `_shifted` — the one offset `schema.py` never saw

```python
def _shifted(term: Term, delta: int) -> Term:
    if isinstance(term, ConstantTerm):
        return term
    if isinstance(term, ScaledTerm):
        return term.model_copy(update={"term": _shifted(term.term, delta)})
    new_offset = validate_offset(term.offset + delta)
    return term.model_copy(update={"offset": new_offset})
```

This exists for exactly one caller: crossover evaluation needs a "one bar
earlier" reading of the same term. `ConstantTerm` shifts to itself — a
constant's "previous value" is definitionally itself, and this is the correct
semantics for something like `RSI crosses_below 30`: the comparison is "was RSI
at or above 30 last bar, is it below 30 now," not "was the number 30 somehow
different last bar." `ScaledTerm` recurses into its wrapped term, since the
`ScaledTerm` itself carries no offset to shift — shifting `0.6 × range[-2]`
"back one bar" has to mean shifting the `-2` inside `range`, producing
`0.6 × range[-3]`, which is exactly what the recursive call does.

The `validate_offset(term.offset + delta)` line is where this function earns its
keep as something more than a mechanical helper: `term.offset + delta` is a
number that never existed anywhere in the original rule and that `schema.py`
never validated, because `schema.py` only ever sees a term's own literal,
declared offset. This was proven concretely: a `PriceTerm(offset=-5)` is
perfectly legal on its own — exactly at `MAX_LOOKBACK`, the deepest allowed
value. Wrapping that same term in a crossing comparison implicitly needs the
reading at `offset=-6`, and `_shifted` correctly raises `ValueError` for it. No
validator in `schema.py` could have caught this in advance, because whether a
term will end up inside a crossing comparison — and therefore needs one bar of
headroom beyond its own declared offset — is a fact about the `Comparison` it's
embedded in, not a fact `schema.py`'s per-term validator can see when it
validates that term in isolation.

### `evaluate_comparison`'s NaN guard

```python
left = resolve_term(cmp.left, ctx)
right = resolve_term(cmp.right, ctx)
if math.isnan(left) or math.isnan(right):
    return False
```

For `gt`/`lt`/`gte`/`lte`/`eq_within`: either side being NaN makes the whole
comparison `False`, never an exception. This matches how indicators actually
behave — a 14-period RSI simply has no value for the first 13 bars of any
dataset, and a rule referencing it during that window should read as "this
condition hasn't been met yet," which is true, not as a crash, which would be
wrong (the strategy hasn't failed; there just isn't enough history yet).

```python
case "eq_within":
    assert cmp.tolerance is not None  # guaranteed by Comparison's own validator
    return abs(left - right) <= cmp.tolerance
```

The `assert` is not new validation — `schema.py`'s `Comparison` model already
guarantees `tolerance` is set whenever `op == "eq_within"` (and rejects it
otherwise). This line exists to make that already-proven invariant visible and
self-documenting exactly where it's relied on, and to fail loudly with a clear
`AssertionError` rather than a confusing `TypeError` from comparing `float` to
`None` if that invariant were ever somehow violated.

### `_evaluate_crossover` — four values, one specific bug it prevents

```python
left_now = resolve_term(cmp.left, ctx)
right_now = resolve_term(cmp.right, ctx)
left_prev = resolve_term(_shifted(cmp.left, -1), ctx)
right_prev = resolve_term(_shifted(cmp.right, -1), ctx)

if any(math.isnan(v) for v in (left_now, right_now, left_prev, right_prev)):
    return False

if cmp.op == "crosses_above":
    return left_prev < right_prev and left_now > right_now
return left_prev > right_prev and left_now < right_now
```

Crossing detection needs all four values — both sides, both the current bar and
one bar earlier. All four must be non-NaN, or the result is `False`. This is not
a generic "NaN is scary" precaution; it prevents a specific, named bug from the
Stage 3 plan's own empirical findings. In Python, `float("nan") < x` and
`float("nan") > x` are *both* `False`, for any `x`. A naively-written crossing
check — `left_now > right_now and not (left_prev > right_prev)` — can therefore
spuriously fire during warmup: if `left_prev` is NaN because there isn't enough
history yet, `left_prev > right_prev` evaluates to `False`, so `not False` is
`True`, and the check concludes "wasn't above before, is above now" purely
because a value was *missing*, not because a real crossing happened. This was
proven directly: a context with exactly one bar of indicator history (so the
"previous" reading resolves to NaN) with the current reading genuinely above the
threshold was confirmed to correctly evaluate `False`, not the spurious `True`
an unguarded check would have produced.

The thresholds themselves use strict `<` and `>` for the "previous" side, not
`<=`/`>=` — matching `backtesting.lib.crossover()`'s exact semantics from Stage
2 (documented in [stage-2/step-02-engine-and-strategy.md](../stage-2/step-02-engine-and-strategy.md)):
true only on the specific bar the crossing occurs, not on every bar the
relationship happens to hold. Keeping this identical to the already-proven
Sacred-Gate-1 engine means a strategy author reasoning about crossing behavior
doesn't need to hold two slightly different definitions in their head depending
on which layer of the system they're thinking about.

### `evaluate_condition` — and why "exit" isn't a concept this file has

```python
def evaluate_condition(cond: Condition, ctx: BarContext) -> bool:
    if cond.kind == "leaf":
        assert cond.comparison is not None
        return evaluate_comparison(cond.comparison, ctx)
    assert cond.children is not None
    values = (evaluate_condition(child, ctx) for child in cond.children)
    return all(values) if cond.kind == "and" else any(values)
```

A plain recursive tree walk. `all()`/`any()` on a generator expression
short-circuits correctly on its own — there's no need for a manual loop with an
early `return` to get that behavior. The two `assert`s reinforce invariants
`Condition`'s own `schema.py` validator already guarantees (a leaf always has a
comparison and never has children; a branch always has children and never has a
comparison), the same self-documenting role the `eq_within` assert plays above.

The section 5 verification below proves something about this function worth
stating plainly here: it takes a `Condition` and returns a `bool`. It has no
parameter, no branch, no special case anywhere that asks "is this an entry
condition or an exit condition?" That distinction is not representable in this
function's inputs at all — it exists only one layer up, in `StrategyRule`, which
happens to have two fields (`entry`, `exit`) that both point at `Condition`
objects. This is why, as the debugging narrative below shows, an "exit logic
bug" as a category of defect cannot exist inside this file: there is no code
path capable of treating the two differently.

---

## 4. Two debugging episodes, and the discipline behind both

Both of the incidents below follow the same method used throughout Stage 3 for
the `bbands`/`stoch`/VWAP findings in Component 2 and the thirteen
`ValidationError` cases in Component 3: when something doesn't behave as
expected — or when asked to justify *why* it now does — isolate the smallest
failing unit, get the exact exception type, location, and values involved, and
use that evidence to decide whether the test setup was wrong or the code under
test was wrong. Neither conclusion is assumed; both are established.

### Episode 1: morning star's false negative

A synthetic three-bar OHLC sequence was hand-built to trigger `morning_star`'s
entry condition. `evaluate_condition` returned `False` — not the expected
result. Rather than guess which of the seven flat-ANDed leaf conditions was the
problem, each of the seven was evaluated individually, printing the resolved
`left` and `right` values for each:

```
OK   bar-2 bearish: close[-2]<open[-2]:        left=14.200 right=20.000
OK   bar-2 long body: body[-2]>0.6*range[-2]:  left=5.800  right=3.720
OK   bar-1 small body: body[-1]<0.3*range[-1]: left=0.000  right=0.300
OK   bar-1 gaps down: close[-1]<close[-2]:     left=14.000 right=14.200
OK   bar0 bullish: close[0]>open[0]:           left=15.800 right=11.000
OK   bar0 long body: body[0]>0.6*range[0]:     left=4.800  right=3.120
FAIL bar0 recovers: close[0]>midpoint[-2]:     left=15.800 right=17.100
```

Six of seven were already satisfied. The seventh named its own problem exactly:
bar 0's close (`15.8`) needed to exceed bar `-2`'s midpoint
(`(open[-2] + close[-2]) / 2 = (20 + 14.2) / 2 = 17.1`) and fell short by 1.3.
This is a defect in the hand-picked synthetic numbers, not in `evaluate_condition`
or the resolved values for the other six conditions — confirmed by raising bar
0's close to `18` (clearing `17.1`) and re-running: the entry condition then
evaluated `True`.

A single passing case isn't enough to trust a seven-way `and`, though — a
validator that always returns `True` would also "pass" this test. So a genuine
true-negative was also constructed: a pattern identical in shape except bar `-2`
is bullish rather than bearish, violating exactly the first of the seven
conditions and no others. `evaluate_condition` correctly returned `False` for
this pattern. The true-positive and the true-negative together are what actually
demonstrate the flat-AND composition works — proving one directly implies the
other would still leave open the possibility that the whole tree is either
always-True or always-False regardless of input.

### Episode 2: the exit-condition `KeyError`s, and re-verifying under direct challenge

When the same `FakeCtx` built for the morning star test was reused to evaluate
all four `KNOWN_STRATEGIES`' exit conditions, three of them —
`sma_10_30_crossover`, `rsi_14_30_70`, `rsi_2_10_90`, all of which have
indicator-based exits — raised exceptions, while `morning_star`'s own checks
(which need no indicators at all) had passed cleanly.

Asked directly to confirm — with the same evidentiary rigor as the morning star
debugging, not as an assertion — that this was a test-setup gap and not a logic
defect specific to how exit conditions get evaluated, the original failure was
reproduced with a full traceback rather than re-argued from memory:

```
sma_10_30_crossover: KeyError: ('SMA', frozenset({('length', 10.0)}))
rsi_14_30_70:        KeyError: ('RSI', frozenset({('length', 14.0)}))
rsi_2_10_90:         KeyError: ('RSI', frozenset({('length', 2.0)}))
```

All three raised `KeyError`, at the identical line inside the test double's own
`indicator()` method (`s = self.indicators[key]`) — a plain dict-lookup miss.
That `FakeCtx` instance had been built specifically for the morning star test,
which needs zero indicator data (it's pure candlestick geometry over raw OHLC),
and so its `indicators` dict had never been populated with `SMA`/`RSI` series at
all. This is a categorically different failure from a `ValueError` (which would
point at an offset or lookback bug) or an `AssertionError` (which would point at
a malformed `Condition`) — it is a hard, loud crash on data that was simply
never provided, not a silently-wrong answer that happened to look like a crash.
Combined with the structural fact from section 3 — `evaluate_condition` has no
representation of "entry" versus "exit" anywhere in its logic — this closes the
question without needing to trust an assertion: there is no code path by which
exit-specific behavior could differ from entry-specific behavior, because no
such distinction is expressible in this file at all.

After populating the missing indicator keys with data designed to cross in the
expected direction, all three evaluated correctly: SMA(10) crossing below
SMA(30) fired `True`, RSI(14) crossing above 70 fired `True`, RSI(2) crossing
above 90 fired `True`. The fix was adding test data. `evaluator.py` itself was
not touched.

---

## 5. How the verification gate was satisfied

The following was run interactively (no `test_evaluator.py` yet — Component 8):

- All seven term kinds resolved correctly against hand-computed expected values
  (`ConstantTerm`, `PriceTerm` at two offsets, `BodyTerm`, `MidpointTerm`,
  `RangeTerm`, `ScaledTerm` compounding a factor onto a `RangeTerm`, and
  `IndicatorTerm` resolved through a real `indicator_key`, not a stub).
- The NaN guard confirmed for simple comparisons (`gt` with a NaN operand
  returns `False`; `eq_within` correctly distinguishes inside- versus
  outside-tolerance).
- Three real crossing scenarios against a real registered indicator (RSI, via
  `indicator_key`, not a hand-rolled stub name): fires correctly on a genuine
  cross, correctly does *not* fire when no cross occurs, and fires correctly in
  the opposite direction (`crosses_above`).
- The specific warmup NaN-guard bug proven directly, as described in section 3.
- A crossing evaluated on a `PriceTerm` rather than an `IndicatorTerm`,
  confirming crossover logic isn't accidentally coupled to indicators
  specifically.
- `_shifted`'s `ConstantTerm` no-op and `ScaledTerm` inner-term recursion both
  verified structurally (shifting a `ScaledTerm` produces a new `ScaledTerm`
  whose *inner* term's offset moved, not the outer object gaining an offset it
  doesn't have).
- The mutability-gap proof (`term.offset = 99` post-construction still caught).
- The `MAX_LOOKBACK`-exceeded-via-crossover proof (`offset=-5` legal alone,
  correctly rejected once wrapped in a crossing comparison).
- `morning_star`'s true-positive and true-negative, both described in section 4.
- All three indicator-based `KNOWN_STRATEGIES` exit conditions, verified correct
  after fixing the test data gap described in section 4.
- The full 16-test regression suite (Stages 1–2) stayed green throughout every
  step above, including through the `indicators.py`/`schema.py` relocation.

**What this does not prove.** There is still no automated `test_evaluator.py` —
every result above was produced and reported interactively, not captured as a
`pytest` file that will keep re-proving itself on every future change to
`schema.py` or `indicators.py`. The `FakeCtx` test double's indexing convention
(offset `0` = the most recent element, more negative = further back) was
designed by hand to match what Component 5's real, `backtesting.py`-backed
context will need to provide — but that real implementation doesn't exist yet,
so `BarContext`'s behavior against actual `backtesting.py` machinery remains
unverified. This is the same category of residual risk flagged for VWAP's
`DatetimeIndex` requirement in Component 2: a real constraint identified ahead
of time, not yet exercised through the real execution path because that path
doesn't exist yet.

---

## 6. Interview defense

**Q: Why is `BarContext` a `Protocol` instead of an abstract base class?**

A: Structural typing means nothing needs to inherit from it to satisfy it — the
test double built to verify this file has zero import dependency on
`evaluator.py` beyond the type hint itself, and Component 5's real
implementation, backed by `backtesting.py`'s own array types, only needs to
expose two correctly-shaped methods. An ABC would work functionally identically
at runtime here, but would force every implementation, including throwaway test
doubles, into an explicit inheritance relationship for no behavioral gain.

**Q: Why re-derive and re-validate `_shifted`'s offset instead of just trusting
that crossover always looks back exactly one bar and hardcoding that?**

A: Because the offset a crossing comparison needs isn't always "the term's
literal offset minus one" in a way that's safe to assume without checking — it's
specifically the term's *declared* offset minus one, and that declared offset
could already be as deep as `-MAX_LOOKBACK`. Hardcoding the assumption that this
is always safe would have missed exactly the failure mode this file explicitly
tests for: a term at the lookback boundary, embedded in a crossing comparison,
silently reading one bar further into the past than the system is supposed to
allow.

**Q (hard): Nothing in this codebase actually mutates a `Term` after
construction today. Isn't re-validating offsets in `resolve_term` defending
against a scenario that can't currently happen — the kind of speculative
error-handling this project explicitly says not to write?**

A: The honest answer is that it's defending against a gap that's real *today*,
even though nothing exploits it yet: these models are mutable Pydantic objects
with no `validate_assignment`, so the guarantee "a constructed `Term` always has
a legal offset" is only true immediately after construction, not for the
lifetime of the object — and that was proven, not just reasoned about, by
directly mutating one and watching `schema.py`'s protection do nothing. The
question of whether to defend against it is really a cost comparison: the
defense costs one cheap function call per term resolution; not having it costs a
silent Sacred-Gate-1 violation the moment *any* future code path — a rule-editing
feature, a caching layer that clones and tweaks a `Term`, anything — ever does
mutate one, and that failure would be invisible rather than loud. For an
invariant this close to the project's sacred gate, checking cheaply now against
a gap that's real (even if currently unexploited) is a different category of
decision than writing a `try/except` around something that structurally cannot
occur.

**Q: Why not just make the `schema.py` models `frozen=True` with
`validate_assignment=True` and close this gap at the source, instead of
re-checking in `evaluator.py`?**

A: This is a legitimate alternative, and it would close the gap more
fundamentally rather than compensating for it downstream. It wasn't chosen for
this component specifically because it means reopening and modifying the
already-committed `schema.py` from Component 3, rather than this component
being a self-contained addition — and `validate_assignment=True` adds a real
per-assignment validation cost that would apply on every single assignment for
the lifetime of the object, which matters if Component 5's real implementation
ever needs to construct or adjust `Term`s inside backtesting.py's bar-by-bar
`next()` loop, the hottest path in the whole system. This is a reversible
decision recorded here deliberately, not a dismissal: if a future stage finds
mutation is actually happening somewhere, freezing the models at the source is
the more correct fix, and this file's redundant check would simply become
provably unnecessary rather than wrong.

**Honest weakness:** no automated test file exists for this component yet, same
as `schema.py`. And the `BarContext` implementation used for every proof in this
document is a hand-built fake, not the real thing — every guarantee here is a
guarantee about the *logic*, not yet a guarantee about how that logic behaves
once it's wired to `backtesting.py`'s actual `self.I()` arrays and actual price
data, which is exactly what Component 5 has to prove next.

---

## 7. What comes next and why

Component 5 (`strategies/rule_strategy.py`) is `make_rule_strategy(rule)` — the
function that compiles a validated `StrategyRule` into a real `backtesting.py`
`Strategy` subclass. It will build the real `BarContext`, backed by `self.data`
and precomputed `self.I()` indicator arrays, deduplicating indicators using the
exact same `indicator_key` function defined in this file — not a reimplementation
of it. It will also be the first place VWAP's `DatetimeIndex` requirement
(flagged in Component 2's step explainer) actually has to be solved, since it's
the first component that constructs the real price Series indicators get
computed from.

If `evaluator.py` had a subtle defect in its NaN guard — say, if the crossover
guard only checked the "now" values and not the "previous" ones — it would not
surface here, because nothing in this file's verification touches real
backtesting.py machinery. It would surface only once Component 5 ran a real
backtest: either as a crash, or, more dangerously, as a strategy that trades on
a spurious warmup-period crossing and produces a real trade count and a
plausible-looking Sharpe ratio that isn't actually measuring what the rule
claims to measure. That is the same "looks fine until it doesn't" failure shape
documented for the `bbands`/`stoch` bugs in Component 2 and the offset-bound
reasoning in Component 3 — a defect that passes every check available at the
layer where it was introduced, and only becomes visible once something
downstream actually depends on the thing that was wrong.
