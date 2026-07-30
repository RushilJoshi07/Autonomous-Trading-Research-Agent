# Step 2 — Strategy Rule Schema (Stage 3)

## 1. What this does

`src/backtester/schema.py` defines the format a trading strategy is expressed in:
a typed, validated data structure describing *when to enter a trade* and *when to
exit*, built out of a small closed vocabulary of primitives (indicator values,
raw price references, arithmetic on candle geometry) combined with comparisons
and boolean logic.

Concretely, it is a set of Pydantic models — `Term` (seven kinds), `Comparison`,
`Condition`, `StrategyRule` — plus four fully worked, hand-built examples in
`KNOWN_STRATEGIES` that exercise the schema end to end.

What this is **not**: it does not run a strategy, backtest anything, or compute a
single number from real price data. A `StrategyRule` object, once constructed,
validates that it is *structurally* sound — every offset legal, every indicator
real and in-bounds, every boolean node well-formed — but it is inert. Nothing in
this file knows what `close` actually equals on any given day. That is
`evaluator.py`'s job (Component 4, not yet built) and `strategies/rule_strategy.py`'s
job (Component 5, compiles a validated rule into something `run_backtest` can
execute). This file's only responsibility is: given a candidate rule, is it even
legal to try.

---

## 2. Every meaningful line explained

### The offset-validation helper

```python
def _validate_offset(offset: int) -> int:
    if offset > 0:
        raise ValueError(f"offset must be <= 0 (positive offset is lookahead), got {offset}")
    if offset < -MAX_LOOKBACK:
        raise ValueError(f"offset must be >= -{MAX_LOOKBACK}, got {offset}")
    return offset
```

A free function, not a method, because five different term kinds need the exact
same check and none of them should each carry their own copy of the bound logic.
`offset` means "how many bars back from the bar currently being evaluated" — `0`
is the current bar, `-1` is yesterday, and so on. `MAX_LOOKBACK` (imported from
`indicators.py`, value `5`) caps how far back a rule may reach. The `offset > 0`
branch is checked and raised *before* the lower-bound check specifically so the
error message a caller sees for `offset=1` says "positive offset is lookahead" —
the more informative, more actionable message — rather than a generic
out-of-range message that would also fire for `offset=1` if the bound check ran
first (since `1` is also, technically, `>= -5`... no it isn't the issue there, but
ordering the checks this way keeps each error message specific to what's actually
wrong, positive vs merely-too-negative, rather than collapsing both into one
generic range check).

### `_apply_cross_check`

```python
def _apply_cross_check(cross_check: dict, params: dict[str, float], indicator_name: str) -> None:
    left_key, right_key = cross_check["left"], cross_check["right"]
    if left_key not in params or right_key not in params:
        return
    ...
```

This is the interpreter for the declarative `cross_check` dicts that live on
`IndicatorSpec` entries in the registry (e.g. MACD's `{"type": "less_than",
"left": "fast", "right": "slow"}`). The early `return` when either referenced key
is missing from the rule's `params` is the one genuinely debatable line in this
function — it means "if you don't tell me both values, I won't check the
relationship between them." The alternative (require both be present whenever a
cross_check exists) is discussed in section 3.

### `_OffsetTerm` and why five term kinds inherit it, two don't

```python
class _OffsetTerm(BaseModel):
    offset: int = 0
    @field_validator("offset")
    @classmethod
    def _check_offset(cls, v: int) -> int:
        return _validate_offset(v)
```

`BodyTerm`, `MidpointTerm`, `RangeTerm`, `PriceTerm`, and `IndicatorTerm` all
subclass this and inherit both the field and its validator — Pydantic v2 applies
inherited `field_validator`s to subclasses automatically, without needing to
redeclare them, which is exactly why none of those five classes mention `offset`
at all in their own bodies. `ConstantTerm` does not inherit it because a constant
(e.g. the number `30` in an RSI threshold) has no time dimension — asking "what
offset is the number 30 at" is a category error. `ScaledTerm` doesn't inherit it
either, for a related but distinct reason: its own temporal position is entirely
determined by whatever term it wraps (`ScaledTerm(term=RangeTerm(offset=-2),
factor=0.6)` is "60% of the range two bars ago" — the `-2` lives on the inner
`RangeTerm`, not on the `ScaledTerm` itself). Giving `ScaledTerm` its own
independent `offset` field would create two ways to express the same thing (scale
a term, then separately claim a different offset for the scaled result) with no
clear meaning for what happens when they disagree.

### `IndicatorTerm`'s validator

```python
class IndicatorTerm(_OffsetTerm):
    kind: Literal["indicator"] = "indicator"
    name: str
    params: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_indicator(self) -> "IndicatorTerm":
        spec = CORE_INDICATORS.get(self.name)
        if spec is None:
            raise ValueError(f"unknown indicator {self.name!r}")
        if not spec.verified:
            raise ValueError(f"indicator {self.name!r} is not verified")
        for pname, pval in self.params.items():
            if pname not in spec.params:
                raise ValueError(f"{self.name} has no param {pname!r}")
            lo, hi = spec.params[pname]
            if not (lo <= pval <= hi):
                raise ValueError(f"{self.name}.{pname}={pval} out of bounds [{lo}, {hi}]")
        if spec.cross_check:
            _apply_cross_check(spec.cross_check, self.params, self.name)
        return self
```

`params: dict[str, float] = Field(default_factory=dict)` — same mutable-default
trap as `IndicatorSpec.params` in the registry (a bare `= {}` would share one dict
object across every `IndicatorTerm` instance that doesn't override it).
`@model_validator(mode="after")` runs once Pydantic has already built the object
from its individual fields, which is required here because this validator needs
to see `self.name` and `self.params` *together* — a `field_validator` on `params`
alone couldn't look up which indicator's bounds apply, since it wouldn't have
access to `self.name` at the point a single field is being validated.

The body does exactly four things in order: confirm the name exists in the
registry at all; confirm it's `verified` (this is where an extended-tier
indicator that failed its offline verification gets structurally locked out of
ever appearing in a rule); confirm every param the rule actually supplies is both
a real param name for that indicator and within its declared bounds; and run the
cross-param check if the registry declares one. Notice what it does *not* do:
require that every param the registry lists be present. That's a deliberate
choice, covered in section 3.

### The recursive `Term` union and why two `model_rebuild()` calls are load-bearing

```python
class ScaledTerm(BaseModel):
    kind: Literal["scaled"] = "scaled"
    term: Term
    factor: float
    ...

Term = Annotated[
    Union[IndicatorTerm, PriceTerm, ConstantTerm, BodyTerm, MidpointTerm, RangeTerm, ScaledTerm],
    Field(discriminator="kind"),
]
ScaledTerm.model_rebuild()
```

`ScaledTerm.term` is typed as `Term`, but `Term` — the union of all seven kinds,
including `ScaledTerm` — is not defined until several lines *after* the
`ScaledTerm` class body. This is unavoidable: `Term` cannot exist before
`ScaledTerm` exists (it includes it), and `ScaledTerm` cannot avoid referencing
`Term` (a scaled term has to be able to wrap any other kind of term, including,
syntactically, itself — nesting is only actually forbidden by the validator two
lines later, not by the type system). `from __future__ import annotations` at the
top of the file is what makes this possible at all: it turns every type
annotation in the file into a string that Python doesn't try to evaluate at class-
definition time, deferring the problem. `ScaledTerm.model_rebuild()` is the
explicit "okay, now go resolve those deferred strings" instruction — called right
after `Term` is finally defined, at which point the name genuinely exists in the
module's namespace and Pydantic can build the real validation schema. Skip this
call and `ScaledTerm` would raise a `PydanticUndefinedAnnotation` the first time
anyone tried to actually use it. `Condition` needs the identical treatment for
the identical reason a few dozen lines later (`children: list["Condition"]` — a
condition tree that contains condition trees).

### `ScaledTerm`'s no-nesting guard

```python
@field_validator("term")
@classmethod
def _check_not_nested(cls, v: "Term") -> "Term":
    if isinstance(v, ScaledTerm):
        raise ValueError("ScaledTerm cannot wrap another ScaledTerm (no nesting)")
    return v
```

`field_validator`'s default mode is `"after"` — meaning by the time this function
runs, Pydantic has already fully parsed and constructed whatever was passed for
`term` into one of the seven concrete term model instances. So `isinstance(v,
ScaledTerm)` is checking a real, already-built object, not a raw dict. This is
confirmed working by the test in section 5: `ScaledTerm(term=ScaledTerm(...),
factor=3)` raises exactly this error.

### `Comparison`'s tolerance coupling

```python
@model_validator(mode="after")
def _check_tolerance(self) -> "Comparison":
    if self.op == "eq_within" and self.tolerance is None:
        raise ValueError("eq_within requires tolerance to be set")
    if self.op != "eq_within" and self.tolerance is not None:
        raise ValueError(f"tolerance is only valid for eq_within, not {self.op!r}")
    return self
```

The first branch is what the Stage 3 plan explicitly calls for testing. The
second branch is an addition beyond the plan's stated requirement — discussed in
section 3.

### `Condition`'s shape validator

```python
@model_validator(mode="after")
def _check_shape(self) -> "Condition":
    if self.kind == "leaf":
        if self.comparison is None:
            raise ValueError("leaf condition requires a comparison")
        if self.children:
            raise ValueError("leaf condition must not have children")
    else:
        if self.comparison is not None:
            raise ValueError(f"{self.kind} condition must not have a comparison")
        if not self.children or len(self.children) < 2:
            raise ValueError(f"{self.kind} condition requires at least 2 children")
    return self
```

`Condition` has both a `comparison` field and a `children` field, both `Optional`,
because a single Pydantic model represents two structurally different node types
(leaf vs. and/or) — the alternative, a discriminated union of two separate
`LeafCondition`/`BranchCondition` classes, is discussed and rejected in section 3.
This validator is what turns "both fields optional" from a loophole into a
closed, exhaustive set of legal shapes: a `Condition` can only ever be a pure leaf
(comparison, no children) or a pure branch (children, no comparison) — never both,
never neither.

### `StrategyRule`'s exit requirement

```python
@model_validator(mode="after")
def _check_exit(self) -> "StrategyRule":
    if self.exit is None and self.exit_after_bars is None:
        raise ValueError("StrategyRule requires exit and/or exit_after_bars")
    if self.exit_after_bars is not None and self.exit_after_bars <= 0:
        raise ValueError("exit_after_bars must be positive")
    return self
```

Confirmed both branches independently in testing: a rule with neither `exit` nor
`exit_after_bars` correctly fails, and — separately — `SMA_CROSSOVER` (which sets
only `exit`, no `exit_after_bars`) and `MORNING_STAR` (which sets only
`exit_after_bars`, no `exit`) both construct successfully, proving "at least one"
really does mean "either alone is fine," not "we'll silently require both anyway."

### The `_leaf` helper and `KNOWN_STRATEGIES`

```python
def _leaf(left: Term, op: str, right: Term, tolerance: float | None = None) -> Condition:
    return Condition(kind="leaf", comparison=Comparison(left=left, op=op, right=right, tolerance=tolerance))
```

Pure convenience — every leaf condition in all four known strategies is
`Condition(kind="leaf", comparison=Comparison(...))`, and writing that out longhand
fourteen times (three simple strategies × 2 conditions each, plus morning star's
seven) would bury the actual strategy logic under repeated boilerplate. This
function carries no validation of its own; it's just a shorter way to call
existing validated constructors.

The three simple strategies (`SMA_CROSSOVER`, `RSI_14_30_70`, `RSI_2_10_90`) each
use only `IndicatorTerm`, `ConstantTerm`, and the `crosses_above`/`crosses_below`
ops. `MORNING_STAR` is qualitatively different — covered in full in section 3,
since it's the one piece of this component that actually justifies most of the
term vocabulary existing.

---

## 3. Design decisions and rejected alternatives

### Params are optional per-key, not required-complete

The alternative considered: require a rule's `IndicatorTerm.params` to contain
*every* key the registry declares for that indicator, defaulting nothing. This
would make every `IndicatorTerm` fully self-describing — reading the rule alone
tells you every parameter value in play, with no need to also know pandas-ta's
own defaults. It was rejected because it would force every rule author (today,
me writing `KNOWN_STRATEGIES` by hand; eventually, the LLM in Stage 5 proposing
hypotheses) to restate values they don't actually care about varying. `RSI(14)`
only needs to say `length=14`; forcing it to also enumerate every other RSI
parameter pandas-ta happens to expose would be noise, not information.

The cost of the chosen design shows up specifically in `_apply_cross_check`:
when a cross-param constraint exists (MACD's `fast < slow`) but the rule only
supplies one of the two params, there's genuinely nothing to check — the
function returns early rather than erroring, confirmed in testing with
`IndicatorTerm(name="MACD", params={"fast": 12})` constructing successfully. This
means the schema is *trusting* that pandas-ta's own default for `slow` (26) is
sane and does satisfy `fast < slow` for whatever `fast` value the rule supplied.
That trust is not verified anywhere in this file. If it were ever wrong — if a
future pandas-ta version shipped a nonsensical default — the failure wouldn't
appear here; it would surface downstream, either as a pandas-ta runtime error in
Component 4/5, or worse, as a rule that runs to completion and produces a
plausible-looking but wrong result. This is a real, disclosed residual risk, not
a solved problem — matching the pattern from Component 2's registry work, where
"it didn't raise" was repeatedly shown to be insufficient proof that something
works correctly.

### `Comparison` rejects `tolerance` on non-`eq_within` ops, not just requiring it on `eq_within`

The Stage 3 plan's test list only calls for "eq_within no tolerance" as an error
case — i.e., the plan only requires the first half of `_check_tolerance`. The
second half (`tolerance` set on `gt`/`lt`/etc. is itself an error) is an addition.
The alternative — silently ignoring `tolerance` when the op doesn't use it — was
rejected for the same reason bbands' un-corrected `std` parameter would have been
a problem in Component 2: a field that appears to configure behavior but is
quietly dropped is a trap for exactly the kind of "confirm this rule does X" claim
this project is built to prevent. A rule author who writes `Comparison(left=...,
op="gt", right=..., tolerance=0.05)` almost certainly believes the `0.05` is doing
something. Rejecting it outright, with a clear error naming the actual problem,
is more honest than accepting it and silently doing nothing with it. The
reversibility here is total — removing this stricter branch later, if it ever
proves too aggressive, is a one-line deletion with no data migration implied.

### `and`/`or` require at least 2 children, not just "not empty"

The plan's test list calls for "and-node no children" as an error case, which a
`len(children) >= 1` check would already satisfy. The chosen `>= 2` bound is
stricter than what was asked for. Rejected alternative: allow exactly one child.
A single-child `and` or `or` is not incorrect, but it is redundant — `Condition(
kind="and", children=[X])` and `X` itself mean exactly the same thing, evaluate to
exactly the same boolean, for every possible input. Allowing it would mean the
same logical rule could be expressed in infinitely many structurally different
ways (wrap anything in as many single-child `and` nodes as you like), which is
exactly the kind of ambiguity that would complicate rule deduplication later
(architecture.md's Step 2 requires detecting when a newly proposed hypothesis
duplicates an already-tested one — that comparison is much harder if logically
identical rules can have arbitrarily different tree shapes). Requiring `>= 2`
costs nothing for any of the four known strategies (morning star's `and` has 7
children; nothing anywhere needs a single-child branch) and closes off that
ambiguity at zero present cost.

### One `Condition` model with two optional fields, not two subclasses in a discriminated union

The alternative: model leaves and branches as two entirely separate Pydantic
classes — `LeafCondition(comparison=...)` and `BranchCondition(kind, children)` —
joined by their own discriminated union, mirroring exactly how the seven `Term`
kinds are handled. This is more consistent with the rest of the file stylistically,
but it was rejected for `Condition` specifically because branches recursively
contain more `Condition`s of *either* shape — `children: list[Condition]`, not
`list[LeafCondition | BranchCondition]` written out awkwardly at every use site.
A single `Condition` model where `kind` determines which combination of
`comparison`/`children` is legal (enforced by `_check_shape`, not by the type
system) keeps the recursive `children: list["Condition"]` annotation simple and
matches how `list[Condition]` reads naturally as "a list of well-formed
conditions," without the reader needing to hold a union type in mind at every
recursive point. The cost: illegal states (comparison and children both set, or
neither) are only prevented by the validator's *runtime* check, not by the type
system refusing to construct them at all — a real, if narrow, gap between what
Pydantic's static shape allows and what the domain actually permits. `_check_shape`
closes that gap immediately at construction time, so the practical difference is
small, but it is there.

### Morning star: flat `and`, not nested sub-groups

All seven of morning star's leaf comparisons live directly under one top-level
`and`, rather than grouped into three sub-`and`s (one per bar) joined by an outer
`and`. Nesting was considered because it visually clusters the checks by which
candle they describe. It was rejected because every one of the seven conditions
is unconditionally required — there is no point in the pattern's definition where
"either this group of checks or that group" applies; it is strictly "all of the
following, together." Grouping the same seven required-and-unanimous conditions
into three arbitrary nested boxes wouldn't change what the rule asserts, would
add three extra `Condition` objects to construct and validate for no semantic
gain, and — combined with the ">= 2 children" rule above — would need each
sub-group to independently justify having at least two members in it, which they
do here but wouldn't in general. Flat is the more honest shape for "these seven
things are all simultaneously required": one `and`, one clear boundary, nothing
implied by the tree structure that isn't actually true of the rule.

### Why morning star needs five term kinds the other three strategies never touch

`SMA_CROSSOVER`, `RSI_14_30_70`, and `RSI_2_10_90` are all expressible with just
`IndicatorTerm`, `ConstantTerm`, and a crossing or threshold comparison — the
entire vocabulary a purely indicator-driven strategy needs. Morning star is a
*candlestick geometry* pattern: it is defined entirely in terms of relationships
between raw `open`/`high`/`low`/`close` values across three consecutive bars, and
those relationships are inherently relative ("a *long* bearish candle," "a
*small*-bodied star") rather than absolute. `BodyTerm` and `RangeTerm` exist
specifically to make "how big is this candle's body, relative to its own range"
expressible at all; `MidpointTerm` exists because "did the recovery candle close
back above the first candle's midpoint" is the actual textbook definition of the
pattern's confirmation bar, not an approximation of it; `ScaledTerm` is what
turns "long" and "small" from vague adjectives into checkable thresholds (`body >
0.6 × range`, `body < 0.3 × range`); and multi-offset `PriceTerm` (`offset=-2`,
`offset=-1`, `offset=0` all appearing in the same rule) is what makes a
*three-bar* pattern expressible in a schema that otherwise, strategy by strategy,
might only ever look at the current bar. This is precisely why the Stage 3 plan
treats morning star as the expressiveness proof and makes the gate fail outright
if it cannot be represented: a schema that only supported the first three known
strategies would look complete right up until someone tried to express a
candlestick pattern, and would then be revealed as missing half its intended
vocabulary.

---

## 4. Concepts introduced

### Discriminated unions (tagged unions)

A discriminated union is a set of otherwise-unrelated types that share one common
field (here, `kind`) whose value tells a parser exactly which type it's looking
at, with no guessing required. `Term = Annotated[Union[...], Field(discriminator=
"kind")]` tells Pydantic: when validating something that's supposed to be a
`Term`, read `kind` first, then validate against exactly that one matching model.
This matters for two reasons covered in Component 2's explanation and reconfirmed
here: validation errors are precise (a malformed `IndicatorTerm` produces an error
about `IndicatorTerm` specifically, not a confusing cascade across all seven
possible shapes), and — looking ahead to Stage 5 — an LLM emitting a rule as
structured JSON is being asked to pick one of seven named shapes and fill in
exactly its fields, a far more constrained and checkable task than filling in an
unknown subset of a single object with every possible field made optional.

### Forward references and deferred annotation evaluation

Normally, Python evaluates a type annotation the moment it's read — which is a
problem the instant a type needs to reference something that doesn't exist yet
(`ScaledTerm` referencing `Term`, which is defined later; `Condition` referencing
itself, which doesn't fully exist until its own class statement finishes). `from
__future__ import annotations` (PEP 563) changes every annotation in the file
into a plain, unevaluated string at definition time. Pydantic then needs to be
told explicitly when it's safe to go back and resolve those strings into real
types — that's what `model_rebuild()` does, and it has to be called *after* the
referenced name genuinely exists in the module namespace, which is why both calls
in this file sit immediately after the class or type alias they depend on, not
at the top or bottom of the file arbitrarily.

### Why a validated-but-inert object still matters

A `StrategyRule` that validates successfully has proven something real and
non-trivial — every indicator name is real and verified, every parameter is in
bounds, every offset stays within the lookahead limit, every boolean node is
well-formed, there's a defined way to exit the trade — without having executed a
single line of backtesting logic or touched one row of price data. This is the
practical meaning of "vagueness stops at the human boundary" from the project's
governing principles: by the time a rule reaches this schema, English is gone and
everything left is checkable. What validation here does *not* prove is that the
rule is a *good* strategy, or even that it will *run* correctly against real
data — those are Component 4/5's job, and no amount of schema validation
substitutes for actually executing the rule.

---

## 5. How the verification gate was satisfied

No formal automated test file exists yet for this component — `tests/backtester/
test_schema.py` is Stage 3 Component 8, built later alongside the evaluator and
rule-strategy tests. What was verified interactively, matching the same
verification depth `test_schema.py` will eventually formalize:

- All four `KNOWN_STRATEGIES` (`sma_10_30_crossover`, `rsi_14_30_70`,
  `rsi_2_10_90`, `morning_star`) construct and pass validation without error.
- All thirteen `ValidationError`/`ValueError` cases the Stage 3 plan calls for
  were individually triggered and confirmed to raise: unknown indicator name,
  out-of-range param, MACD `fast >= slow` via the cross-param check, a leaf
  condition given children, an `and` node given zero children, an `and` node
  given exactly one child, a `StrategyRule` with neither `exit` nor
  `exit_after_bars`, an offset beyond `-MAX_LOOKBACK`, a positive offset,
  `eq_within` with no tolerance, `tolerance` set on a non-`eq_within` op, a
  nested `ScaledTerm`, and a `ScaledTerm` with `factor <= 0`.
- Two cases that must *not* error were also confirmed to succeed: an
  `IndicatorTerm` with no params at all (falling back to pandas-ta defaults), and
  a MACD `IndicatorTerm` supplying only `fast` (the cross-check correctly skips
  rather than erroring on incomplete information).
- The full existing test suite (`pytest tests/`, 16 tests spanning Stages 1–2)
  was re-run after adding this file and passed unchanged — confirming the new
  module has no import-time side effects or naming collisions that broke
  anything already working.

**What this does not prove:** that these thirteen error cases and two success
cases are the *complete* set of ways this schema could be misused — they are the
set the Stage 3 plan specifically anticipated, not an exhaustive fuzz of every
possible malformed input. It also does not prove the schema is expressive enough
for strategies beyond the four written here; morning star's role as the
"expressiveness proof" specifically means the gate considers the vocabulary
sufficient because *this one pattern* is representable, which is evidence, not a
guarantee, about strategies not yet attempted. Most importantly: none of this
verification touches actual price data or actual bar-by-bar evaluation — it
proves these objects are legal to construct, not that they behave correctly once
something tries to evaluate them, because nothing capable of evaluating them
exists yet.

---

## 6. Interview defense

**Q: Why a hand-rolled discriminated union of seven term types instead of a
small general-purpose expression language (arbitrary `+`, `-`, `*`, `/`, nested
freely)?**

A: A general expression language would be strictly more powerful, but it directly
works against the project's "vagueness stops at the human boundary, everything
past it is typed and deterministic" principle in a specific way: an unbounded
grammar has an unbounded space of possible rules, which is much harder to
statically bound-check (arbitrary nesting depth, division by zero, numeric
overflow) and, more importantly, much easier for an LLM in Stage 5 to construct
into a convoluted, unauditable expression that *looks* like it's testing
something scientific but is actually curve-fit numerology assembled from enough
free parameters to fit anything. The seven-kind vocabulary here is deliberately
sized to exactly what the four known strategies need, proven by the fact that
it's exactly sufficient to make morning star representable — not more, not less.

**Q: Why does `_apply_cross_check` silently skip the MACD `fast < slow` check
when only one of the two params is supplied, instead of requiring both whenever
a cross_check exists?**

A: Because requiring both would mean every rule using MACD has to restate
`slow` even when it only cares about varying `fast`, which contradicts the
broader "params are optional, not required-complete" design covering every
indicator. The honest tradeoff, which I'd say plainly in an interview rather than
hide: this means the schema trusts pandas-ta's own default value for the
unsupplied param is sane. That trust isn't verified anywhere in this file. It's a
narrow, disclosed gap — the alternative of requiring completeness whenever a
cross_check exists would close it, at the cost of the exact same restatement
problem "params optional" was designed to avoid everywhere else.

**Q (hard): You added stricter behavior in two places beyond what the Stage 3
plan's test list explicitly called for — rejecting `tolerance` on non-`eq_within`
ops, and requiring 2+ children instead of just "not empty" for `and`/`or`. Isn't
inventing your own validation rules beyond the approved plan exactly the kind of
silent deviation CLAUDE.md says to avoid?**

A: The distinction I'd draw is between *changing an already-settled design
decision* (which the bbands correction in Component 2 was, and which was
explicitly surfaced and confirmed before being applied) versus *adding a stricter
check in an area the plan left underspecified*, where the plan's test list names
the error cases it definitely wants caught but doesn't claim to be an exhaustive
list of everything that should be rejected. Both additions here are strictly
narrowing — they reject strictly more inputs than the plan's minimum bar, never
fewer — and both are trivially reversible (deleting one `if` branch each) if they
ever prove too strict for a real strategy. That said, the more defensible version
of this answer is: this is exactly the kind of judgment call that's worth a quick
"here's what I added and why, flag it if you disagree" rather than assuming
silence means agreement — which is what this document is doing right now, and
what I'd do explicitly in the moment if either addition were less obviously safe
than these two are.

**Honest weakness:** this component has zero formal automated tests committed to
the repo. Everything in section 5 was run interactively and reported, not
captured as a `pytest` file that will keep proving itself true on every future
change to `indicators.py` or `schema.py`. Until `test_schema.py` exists (Stage 3
Component 8), a future edit could silently break one of these thirteen
guarantees and nothing would catch it automatically. This is a scheduling
decision matching the plan's stated build order, not an oversight, but it's a
real, current gap, not a hypothetical one.

---

## 7. What comes next and why

Component 4 (`evaluator.py`) is where these objects stop being inert. It adds
pure functions — `resolve_term`, `evaluate_comparison`, `evaluate_condition` —
that take a `Term`/`Comparison`/`Condition` plus a `BarContext` (something that
can answer "what was the price/indicator value at this offset") and actually
compute a number or a true/false. This is also where the NaN-guard logic lives
(any comparison with a NaN operand is `False`; `crosses_above`/`crosses_below`
need all four values — current and previous, both sides — non-NaN) and where
positive-offset rejection gets re-enforced a second time, at evaluation time, as
the belt-and-suspenders defense matching Stage 2's `exclusive_orders=True`
philosophy: the schema already prevents a positive offset from ever being
constructed, but the evaluator checks again anyway rather than trusting that
nothing upstream could ever hand it one.

If this schema were subtly wrong — say, if the offset bound accidentally allowed
`offset=1` — the failure would not show up here, because nothing here reads
actual price data. It would surface only once Component 4/5 tried to resolve
that term against a real bar and either read past the end of available data (a
crash) or, more dangerously, silently read a value that happens to exist in
memory but represents a future bar relative to the one being evaluated (a
correctness bug that produces a real, plausible-looking, wrong Sharpe ratio).
That is the same "looks fine until it doesn't" shape as the un-corrected `bbands`
`std` parameter and the `stoch d=1` bug from Component 2 — a defect that passes
every check available at the layer it was introduced and only becomes visible
once something downstream actually depends on the thing that was wrong.
