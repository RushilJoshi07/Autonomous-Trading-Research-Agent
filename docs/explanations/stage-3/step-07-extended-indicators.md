# Step 7 — Extended Indicator Generation and Verification (Stage 3)

## 1. What this does

Component 2 built a **core registry** of ~29 hand-picked, hand-verified pandas-ta
indicators in `src/backtester/indicators.py` — SMA, RSI, MACD, bbands, and so on.
That covers the well-known indicators, but pandas-ta actually ships roughly 150
functions, and the product's promise (`docs/architecture.md` §1) is "any strategy
in plain English," not "any strategy using one of 29 hand-picked functions." This
component builds the **extended tier**: a pipeline that safely admits the rest of
pandas-ta into the registry, without ever trusting a claim about what a parameter
does until that claim has been executed and checked.

The pipeline is two scripts, run in order, each one-time and offline:

- `scripts/generate_extended_indicators.py` — deterministic introspection
  (which functions exist, what OHLCV inputs each needs, which parameters are
  numeric and tunable) plus exactly one bounded LLM call per batch of candidates,
  to propose `(min, max)` bounds for parameters code has already identified as
  numeric. Writes `src/backtester/extended_indicators.py`, every entry starting
  `verified=False`.
- `scripts/verify_extended_indicators.py` — executes every candidate for real
  against synthetic OHLCV data and checks four things empirically. Only entries
  that pass all four flip to `verified=True`. Rewrites the same file in place.

Neither script runs at request time, and nothing here is a new LLM dependency for
the trading system itself — this is the same bounded exception `CLAUDE.md` and
`docs/architecture.md` already record: "Stages 1–3 use no LLM" is amended to allow
exactly one offline, build-time use, verified by execution rather than trusted.

What this component is **not**: it does not touch `KNOWN_STRATEGIES`, the Stage 3
gate script, or any of the four literature strategies — those still only reference
core indicators. It does not add a formal `test_evaluator.py` or expand
`test_rule_strategy.py` into its full planned form — those stay deferred to the
dedicated test-suite pass (plan §8), for reasons in section 3 below. And critically,
it does not make extended indicators available by accident — a separate, deliberate
wiring change (section 2) was required before any of this generated data could
actually be used by a rule.

---

## 2. Every meaningful line explained

### `indicators.py`: the `open_` alias fix

```python
_PARAM_ALIASES = {"open_": "open"}

def _infer_inputs(fn: Callable) -> tuple[str, ...]:
    sig = inspect.signature(fn)
    result = []
    for name, param in sig.parameters.items():
        canonical = _PARAM_ALIASES.get(name, name)
        if canonical in _PRICE_FIELDS and param.default is inspect.Parameter.empty:
            result.append(canonical)
    return tuple(result)
```

pandas-ta names its open-price parameter `open_`, not `open` — it avoids shadowing
the Python builtin. Before this fix, `_infer_inputs` compared each parameter's raw
name directly against `("open", "high", "low", "close", "volume")`, so `open_`
never matched anything. Any function requiring open price (`ha`, `cdl_pattern`,
`bop`, `brar`, and ten others) looked, to this registry, like it needed zero
inputs — and the moment `rule_strategy.py` tried to actually call it with no
arguments, it would raise a plain `TypeError: missing required positional
argument`, with no indication the real problem was a silent misclassification one
layer up.

This bug is not new in this component — it has existed since Component 2, latent,
because no *core* indicator happens to need open price. It stayed invisible for
exactly the reason latent bugs usually do: nothing exercised the code path. The
extended-indicator sweep, which walks essentially all of pandas-ta rather than a
curated 29, was the first thing that ever called `_infer_inputs` on a
`open_`-requiring function. Fixing it here, in the shared `indicators.py`, means
every future caller — core or extended — gets the correct answer, not a
special-cased workaround in the extended-tier code.

### `_numeric_tunable_params`: detection has to use the annotation, not the default

```python
def _is_numeric_annotation(annotation: object) -> bool:
    if annotation is inspect.Parameter.empty:
        return False
    args = get_args(annotation) or (annotation,)
    if any(a in (str, bool) for a in args):
        return False
    return any(a in _NUMERIC_TYPES for a in args)
```

The obvious way to ask "is this parameter a tunable number" is to look at its
default value's type — if `length: int = 14`, the default is an `int`, done. This
is exactly what was tried first, and it found **zero** numeric parameters across
129 candidate functions. The reason: pandas-ta's own convention types every
tunable numeric parameter as `Union[int, numpy.integer, float, numpy.floating] =
None` — the *declared default is always `None`*; the real numeric default (14, or
whatever) lives inside the function body, invisible to `inspect.signature`.
Detection has to read the **type annotation**, not the default value, which is
why `_is_numeric_annotation` walks `get_args(annotation)` instead. `bool` is
excluded explicitly, and checked *before* the numeric check, because
`isinstance(True, int)` is `True` in Python — without that ordering, a categorical
on/off flag like `talib: bool` would be misread as a numeric bound.

This detection is still fully deterministic — nothing here is an LLM guess. The
LLM's only job, downstream, is proposing bounds for exactly the parameter *names*
this function returns; it never decides which parameters are tunable in the first
place.

### `_derive_column_prefixes`: why three sample points, not two

```python
def _derive_column_prefixes(*results) -> list[str] | None:
    if not results:
        raise ValueError(...)
    first = results[0]
    if not isinstance(first, pd.DataFrame):
        return None
    prefixes = list(first.columns)
    for other in results[1:]:
        other_cols = list(other.columns)
        if len(other_cols) != len(prefixes):
            raise ValueError(f"column count changed between calls: {prefixes} vs {other_cols}")
        prefixes = [_common_prefix(p, c) for p, c in zip(prefixes, other_cols)]
    return prefixes
```

Multi-output pandas-ta functions return a DataFrame whose column names embed the
parameter values (`BBL_14_2.0_2.0`), and the existing core-registry convention
(established in Component 2) is a `column_prefix` string matched via
`str.startswith`. The question this function answers: how do you *derive* that
prefix without a human reading the column names by hand?

The first version executed the function at two parameter settings (its declared
min and max) and took the longest common prefix of each output column's name,
matched by position. This reproduced the core registry's own hand-picked prefixes
exactly (`AROOND_`, `CKSPl_`) — until it was tested against `kc` (Keltner
Channels), whose `length` bounds are `(1, 100)`. At `length=1` the column is named
`KCBe_1...`; at `length=100`, `KCBe_100...`. Both strings start with the character
`"1"` — not because `"1"` is stable literal text, but by coincidence, because `100`
happens to start with the same digit as `1`. The two-point diff derived `"KCBe_1"`
as the prefix, which is wrong: it embeds part of the varying parameter, and goes
stale the instant anything calls `kc` with `length=50` or any value not starting
with `1`.

A third sample point — the bounds' midpoint — breaks this in the overwhelming
majority of cases, because it's rare for three genuinely different parameter
values to all share the same leading digits by chance. The function above
generalizes to *any* number of sample points and folds the common-prefix
computation across all of them, so `generate_extended_indicators.py` can supply
`(result_min, result_mid, result_max)` for the normal case and a single result
(folded trivially with itself) for functions with no tunable parameters at all.

The same function also now *raises* — rather than silently misaligning columns by
position — when a column **count** differs between calls. This was found for
real, not hypothetically: `aobv`'s output has 6 columns at one parameter setting
and 7 at another, because its `fast`/`slow` combination collapses or splits an
internal EMA column depending on the values chosen. There is no single fixed
schema that's correct across the whole declared bounds range for that function,
so `generate_extended_indicators.py` catches the raise and skips the whole
indicator with a clear, specific reason, rather than registering something that's
only structurally valid for some parameter choices.

### `normalize_params` and `select_output_column`: moved, not duplicated

Both of these existed already, inline, inside `rule_strategy.py`'s `init()`
method — `normalize_params` converting whole-valued floats to int (numba's
JIT-compiled code paths require a genuine `int` for bar-count parameters, and
raise a `TypingError` on a `float`, even a whole-valued one like `10.0`), and an
inline column-selection block doing the same `str.startswith`-and-check-exactly-1
logic `select_output_column` now does explicitly. Both scripts in this
component needed the *identical* logic — the generation and verification scripts
call pandas-ta functions directly with registry params, exactly like
`rule_strategy.py` does at backtest time. Rather than re-implement either one a
second (and third) time, they were extracted into `indicators.py` as shared,
public functions, and `rule_strategy.py` was updated to import and use them
instead of its own private copies. This is the same "one function, so every
caller agrees" reasoning already applied elsewhere in this codebase (`validate_offset`
being shared between `schema.py` and `evaluator.py` is the Component 4 precedent).

### `registry.py`: merging two tiers without a circular import, and a collision check that actually caught something

```python
from .extended_indicators import EXTENDED_INDICATORS
from .indicators import CORE_INDICATORS

_collision = CORE_INDICATORS.keys() & EXTENDED_INDICATORS.keys()
if _collision:
    raise ValueError(f"extended indicator names collide with core registry: {sorted(_collision)}")

ALL_INDICATORS = {**CORE_INDICATORS, **EXTENDED_INDICATORS}
```

`extended_indicators.py` (generated) imports `IndicatorSpec` from `indicators.py`.
If the merge lived inside `indicators.py` itself, that module would need to import
`extended_indicators.py`, which imports back from `indicators.py` — a cycle. A
new, single-purpose module avoids this cleanly.

The collision check is not defensive filler. An unchecked `{**CORE, **EXTENDED}`
merge would let an extended-tier entry silently shadow a hand-verified core entry
(dict merge order means the second operand wins). This was raised as a specific
requirement during plan review, and it caught a real collision on the very first
real generation run: `aobv`'s output includes a raw, unparameterized `"OBV"`
column (the underlying on-balance-volume series, embedded as one of its seven
outputs) — which derives to registry name `"OBV"`, exactly colliding with the
core `OBV` entry (`ta.obv` directly). Left unchecked, this would have meant that
depending on dict ordering, either the trusted core `OBV` or an unrelated
`aobv`-derived series could silently answer to the same name in a rule.

The check is deliberately applied at **two independent layers**: inside
`generate_extended_indicators.py`'s own `build_specs` (seeded with core names
before generation even starts, so a colliding candidate is skipped with a clear
reason and never written to the file at all), and again here in `registry.py` at
import time, as a second, structurally independent line of defense. If the
generation-time check ever had a gap, the import-time check still fails loudly
rather than silently admitting a shadowed name.

### `schema.py` / `rule_strategy.py`: the swap that actually makes any of this usable

```python
# schema.py
from .registry import ALL_INDICATORS
...
spec = ALL_INDICATORS.get(self.name)

# rule_strategy.py
from ..registry import ALL_INDICATORS
...
spec = ALL_INDICATORS[term.name]
indicators_used = sorted(n for n in used_names if ALL_INDICATORS[n].tier == "core")
extended_indicators_used = sorted(n for n in used_names if ALL_INDICATORS[n].tier == "extended")
```

Before this component, both files looked indicators up in `CORE_INDICATORS`
directly. This is worth stating plainly: **even if `extended_indicators.py` had
existed with 200 perfectly verified entries, none of them could have been used in
a rule before this two-line change** — `schema.py`'s validator would have raised
"unknown indicator" for any extended name, and `rule_strategy.py`'s provenance
line would have raised a bare `KeyError`. This is the actual load-bearing fix in
this component; everything else (generation, verification) produces the *data*,
but this is what makes the data reachable at all. It was verified directly,
end-to-end: a `StrategyRule` referencing `AROOND` (an extended, verified
indicator) validates, compiles via `make_rule_strategy`, and runs 21 real trades
through `run_backtest`, with `extended_indicators_used == ['AROOND']` on the
result.

### `scripts/_extended_codegen.py`: shared synthetic data and one renderer

Both scripts need identical synthetic OHLCV data and an identical way of writing
`extended_indicators.py`'s source text — generation writes the file once,
verification rewrites the same file in place after flipping `verified` /
`verified_on` / `lib_version` per entry. If each script had its own renderer,
they could silently drift apart in how a spec gets serialized. One shared
function (`render_extended_indicators_module`) means both always agree.

The synthetic data defaults to **1500 bars**, not a smaller round number. It
started at 300 (matching an earlier informal test convention), and at 300 bars,
dozens of otherwise-reasonable LLM-proposed bounds — `dpo` at `length=200`,
`squeeze_pro`, `stochrsi`, and others — failed at generation time with errors like
`ValueError: Length of values (0) does not match length of index (300)`, purely
because a 300-bar window doesn't leave enough room for a length-200 lookback plus
warmup. This wasn't a bounds problem or a bug in the indicator — `length=200`
(roughly one trading year) is a completely standard real-world setting, and
production rules will run against years of real daily data. The dataset used to
*test* the bounds was simply too small relative to the bounds being tested.
Raising it to 1500 bars (~6 years) gave genuine headroom and is more
representative of production use, not just a workaround.

`param_midpoint` — used both as the third sample point for column-prefix
derivation and as the "hold everything else fixed" baseline value during
sensitivity testing — rounds to the nearest whole number rather than computing
the exact arithmetic midpoint. `(5.0 + 100.0) / 2 = 52.5` looks harmless, but many
pandas-ta parameters (`p`, `q`, `fast`, `slow`, any length-like count) require a
genuine `int` internally for array slicing, and `normalize_params` only converts
*whole-valued* floats to int — `52.5` stays a non-whole float and breaks anything
doing integer slicing. This was caught for real: `cksp`'s `p` baseline at `52.5`
raised `"cannot do slice indexing on DatetimeIndex with these indexers [0] of type
int"`. Rounding first, then normalizing, fixes this, and is safe for genuinely
fractional parameters too (a whole int and the nearby whole float are numerically
indistinguishable — the same precedent `normalize_params` itself already relies
on) — the only cost is a small amount of baseline-precision, never asserted to
matter for "roughly centered."

### `generate_extended_indicators.py`: classification, batching, and honest skip accounting

Classification walks every `pandas_ta.Category` function name, subtracts the
names already used by `CORE_INDICATORS` (derived from the registry itself, not a
hand-maintained duplicate list — so it can never silently drift out of sync with
Component 2's actual entries), executes each remaining candidate once on synthetic
data, and buckets the result: no OHLCV input (skip — these are meta-indicators
over other series, like `long_run`/`short_run`), execution failure or `None`
return (skip, with the real exception recorded), unsupported return type (skip —
see the `ichimoku` case below), zero tunable numeric parameters (register
directly, no LLM call needed for 7 functions), or one-or-more tunable parameters
(queued for a batched LLM bounds proposal).

Bounds proposals are batched in chunks of ~20 candidates per `structured_output`
call (~6 calls total for 119 candidates) rather than one call per indicator (119
round trips — slow, and un-cost-conscious for what's meant to be a cheap one-time
step) or one giant call (risks an oversized/truncated response). Each chunk's
call is wrapped in its **own** `try/except StructuredOutputError` — a validation
failure on chunk 3 is logged and skipped, and the loop continues to chunk 4; it
does not abort the whole run. This was a specific requirement raised during plan
review: the failure mode being guarded against is one bad chunk near the end
silently discarding every successful chunk that ran before it.

Every accepted bound is checked twice before it's trusted: `min < max` at
acceptance time (a degenerate range is rejected outright, not silently kept), and
then the whole indicator is *executed* at the proposed min, mid, and max values —
if that execution raises or returns `None`, the whole candidate is skipped with
the real exception recorded, never registered on the strength of the LLM's word
alone.

`ta.ichimoku` was found, empirically, to return `tuple[DataFrame, DataFrame]` —
not a `Series` or single `DataFrame` like every other pandas-ta function this
sweep touches. This isn't a bug in pandas-ta; Ichimoku Cloud's "leading span"
component is genuinely meant to display *ahead* of the current data, so the
library returns it as a second, differently-shaped object. This is a structural
incompatibility with this registry's Series/DataFrame assumption, not something a
bounds fix can address, so it's now detected explicitly (`isinstance(result,
(pd.Series, pd.DataFrame))`) and excluded with a specific reason, both here and
defensively inside the verify script's own execution helper.

### `verify_extended_indicators.py`: four checks, and why cross-check claims get the same standard as bounds

For every `verified=False` entry:

1. **Execution and shape** — runs at declared min and max together; must not
   raise, and a DataFrame result's `column_prefix` must match exactly one column
   at both ends.
2. **Per-parameter sensitivity** — the generalized bbands lesson. Each declared
   parameter is varied alone, between its min and max, with every *other*
   parameter held at its (rounded) midpoint; if the output doesn't actually
   change, the parameter is dead and the whole entry is rejected — even if the
   function ran without error. This caught real dead parameters at scale, not
   just the two already known from Component 2 (`bbands`' `std`, and `cksp`'s
   `q`, independently re-confirmed here): `KST`'s and `KVO`'s `signal`,
   `KSTS`'s and `KVOS`'s `drift`, `INCREASING`'s `percent`, `MAMA`'s
   `fastlimit`, `PPO`'s and `PVO`'s `signal`, `TRIX`'s/`TSI`'s/`TSV`'s `signal`,
   `THERMO`'s `length`, among others — confirming that pandas-ta silently
   accepting a non-functional keyword argument is a systemic pattern in this
   library version, not a one-off surprise.
3. **Cross-check claims, execution-verified** — the newest check, and the one
   most directly shaped by plan review. A proposed `cross_check` (e.g. `fast <
   slow`) is not accepted just because the LLM proposed it, the way core's MACD
   entry's `fast < slow` constraint was hand-verified back in Component 2. The
   function is run once at a **satisfied** ordering and once at a **violated**
   ordering (both other params held at baseline); if the violated ordering
   executes cleanly and produces output *indistinguishable* from the satisfied
   ordering, the claimed constraint isn't actually load-bearing, and the whole
   entry — not just the `cross_check` field — is rejected. If no valid violating
   combination exists within the declared bounds at all (one side's whole range
   sits entirely below the other's), the constraint is treated as structurally
   guaranteed by the bounds themselves — a stronger guarantee than an execution
   test could give, so it passes without needing one.
4. **Non-NaN after warmup** — at least one finite value in the last 50 bars, at
   both min and max bounds.

Each entry's checks run inside their own `try/except` in `main()`'s loop — one
entry's unexpected failure (of any kind, not just the specific exceptions the
sub-checks anticipate) must not abort the whole run, mirroring the same
per-chunk isolation principle already applied in generation. This turned out to
matter for a genuinely unresolved reason: an early full run of this script
(before the `ichimoku` exclusion existed) crashed the entire Python process with
`SIGTRAP` (exit code 133) partway through, with no Python traceback — a
process-level signal a `try/except` cannot catch, because the interpreter itself
died. The crash did not reproduce once `ichimoku` was excluded and the `main()`
loop was given its own top-level `try/except`, but the precise causal mechanism
was never fully pinned down (most likely numba/LLVM instability under long,
sequential JIT compilation across ~190 functions in one process) — this is
recorded honestly in section 6 as an open question, not a solved one.

---

## 3. Design decisions and rejected alternatives

**Two separate scripts (generate, then verify) instead of one combined pass.**
A single script that proposed bounds and immediately trusted them would be
faster to write, but it collapses the one distinction this whole component
exists to protect: "the LLM proposed this" and "this was checked by execution"
are different claims, and `verified: bool` needs to be a real gate a rule's
validator can enforce (`schema.py` already refuses any `verified=False` entry).
Splitting the scripts also means verification can be re-run independently — for
instance if pandas-ta is upgraded — without re-spending any LLM calls, since
`generate_extended_indicators.py`'s output is a plain, inspectable Python file,
not a black box.

**The LLM proposes bounds and cross-checks only, never anything else.** This
was the plan's own resolved design question, held to strictly here: inputs,
multi-output detection, and parameter existence all come from deterministic
introspection (`_infer_inputs`, `_numeric_tunable_params`,
`_derive_column_prefixes`) — never from the LLM. The alternative (ask the LLM to
propose the whole `IndicatorSpec`, including column names and input lists) was
rejected for the same reason `CLAUDE.md`'s "vagueness stops at the human
boundary" principle exists: everything past the one narrow, bounded exception
should be typed, validated, and — where code *can* determine something for
certain — code, not a language model's best guess.

**`column_prefix` stays a literal `str.startswith` match; it was not generalized
into a pattern-matching scheme.** Two real cases (`tos_stdevall`, `qqe`) have
sibling output columns that are structurally impossible to distinguish via a
literal prefix, because the varying parameter is embedded *before* the letter
suffix that would otherwise tell columns apart (`TOS_STDEVALL_<length>_L_1` vs
`_U_1` — identical up to the digit). A pattern-matching scheme (prefix *and*
suffix, or a regex with a parameter placeholder) could theoretically resolve
this, but it would touch `select_output_column`, every core registry entry, and
the schema's whole mental model of what a `column_prefix` means — a much larger
change for a handful of affected columns. The chosen alternative: detect the
genuine ambiguity, skip exactly the affected columns with a specific, honest
reason ("not prefix-distinguishable from a sibling column"), and keep every
column that *is* expressible. This is consistent with this project's broader
practice of measuring and disclosing a real limitation (the survivorship-bias
coverage-gap disclosure in the data layer is the same shape of decision) rather
than quietly forcing a fix that would complicate a mechanism working correctly
everywhere else.

**Three sample points for column-prefix derivation, not two.** Covered in
section 2 — the two-point version was the first thing built and shipped, and
was only replaced after the `kc` coincidental-digit-prefix collision was found
by testing against the real, full candidate set rather than a handful of
hand-picked examples. Worth stating plainly here: this is exactly the kind of
bug that a smaller, curated test set would never have surfaced, and it's why
this component's own verification (section 5) leans on running the real
pipeline against the real ~150-function catalogue rather than a representative
sample.

**The collision check lives in two places, not one.** Discussed in section 2.
The alternative — checking only in `registry.py` at import time — would still
have caught the `aobv`/`OBV` collision (the ValueError at import blocked the
whole test suite from even collecting, which is how the collision was actually
found). But catching it *only* there means every future collision surfaces as a
hard import-time crash of the whole registry, discovered by whoever happens to
run the test suite next, rather than as a clear, attributed skip reason in
`generate_extended_indicators.py`'s own summary output at the moment it
happens. Seeding the generation script's own `used_names` with core names means
the person running generation sees "aobv.OBV collided with core" immediately,
in context, rather than debugging an opaque import failure later.

**Chunked, fault-isolated LLM batching instead of one call per indicator.**
Covered in section 2. The per-chunk `try/except` was a specific requirement
raised during plan review, not a default design choice — the first version of
this script wrapped the whole chunking loop's *body* correctly already, but the
explicit ask was to confirm this in writing and verify it, since a subtly wrong
version (e.g., one `try` wrapping multiple chunks) would look identical in the
common case and only fail visibly the first time a later chunk broke.

**Deferring the rest of the formal test suite (plan §8) rather than building
all five files now.** This was an explicit, reasoned decision, not a
restatement of "we'll get to it eventually" — raised directly during plan
review as a gap worth naming precisely. `test_indicator_core.py` and
`test_schema.py` shipped *with* this component because it edits the exact
behavior they'd cover (`_infer_inputs`'s alias fix, `schema.py`'s registry
swap) — shipping a behavior change to already-working code with zero
regression coverage protecting it is a real risk, not a hypothetical one, and
it's sharper here than usual because existing code is being *modified*, not
just added to. A minimal `test_extended_indicators.py` shipped too, covering
only the adversarial case (the verifier's own core correctness claim). What's
still deferred: `test_evaluator.py` (evaluator.py isn't touched by this
component at all) and the full `test_rule_strategy.py` expansion (its change
here is a mechanical import-source swap, already covered indirectly by
`test_schema.py` plus the manual end-to-end check in section 2). Both remain
correctly pending for the dedicated plan §8 pass, not overdue.

---

## 4. Concepts introduced

**Cross-region inference profiles (Bedrock).** Covered already for the Sonnet
model in Component 7's explainer, and it recurred here for a different model:
Bedrock rejects on-demand invocation of certain models by their bare
foundation-model ID, requiring a cross-region "inference profile" ID instead.
The first attempt at this component used `"claude-haiku-4-5-20251001"` (a bare
ID) and every one of the 6 batched calls failed with `"The provided model
identifier is invalid."` The fix — `"us.anthropic.claude-haiku-4-5-20251001-v1:0"`
— was looked up directly via `aws bedrock list-inference-profiles`, filtered for
`haiku-4-5`, not guessed from the pattern that happened to work for Sonnet. The
lesson generalizes: every Claude model used on this project's Bedrock account
needs its own inference-profile ID looked up, not assumed from another model's.

**numba JIT compilation and integer typing.** pandas-ta uses `numba` to
JIT-compile some of its inner loops (`sma`'s rolling-window implementation is
one). numba-compiled code is far stricter about argument types than ordinary
Python — a bar-count parameter typed as accepting `float` at the Python level
can still fail deep inside a compiled kernel if it receives a non-whole float,
because the compiled code path genuinely expects an integer array index or
buffer size. This is why `normalize_params` (whole-valued float → int) exists at
all, and why the *rounded* midpoint (this component's addition) matters — a
non-whole baseline like `52.5` breaks the same class of function even after
`normalize_params` runs, because `52.5` was never whole to begin with.

**A "dead" parameter.** A parameter a library accepts without error, but which
has no actual effect on the output — first found in Component 2 (`bbands`'
`std`), and confirmed here to be a systemic pattern across roughly a dozen
functions, not a one-off surprise. The mechanism (`ta.<fn>(..., **kwargs)`
silently absorbing an unused keyword) is a known pandas-ta looseness, not a bug
unique to any single indicator. The only reliable way to catch this is
execution: run the function at two genuinely different values for the
parameter in question and confirm the output actually differs. "It didn't
raise" is never proof a parameter works.

**A process-level crash vs. a Python exception.** A `try/except` in Python can
only catch things the Python interpreter itself raises. A native crash inside a
C extension or a JIT-compiled code path (numba compiles to LLVM) can terminate
the whole process with an OS signal — `SIGTRAP`, exit code 133, in this
component's case — before the interpreter ever gets a chance to raise anything
catchable. This is why `main()`'s per-entry `try/except` in the verify script,
while correct and necessary, would **not** have protected against the crash
this component actually hit; only removing the specific function
(`ichimoku`) whose presence coincided with the crash actually stopped it.

---

## 5. How this component was verified

Both scripts were run for real, not mocked, against the actual installed
pandas-ta (0.4.71b0) and the actual Bedrock account: `generate_extended_indicators.py`
produced 193 candidate entries from 129 non-core pandas-ta functions (22 legitimately
skipped, each with a specific, inspected reason — not a silent drop), and
`verify_extended_indicators.py` then verified 127 of those 193 for real, executing
every entry's checks against synthetic data and rejecting 66 with specific,
readable reasons (dead parameters, ambiguous shape, insufficient warmup room,
and others). Sample entries and the full adversarial test result were shown
directly, not just summary counts, before this component was signed off as
complete.

The end-to-end path was verified directly: a `StrategyRule` referencing a
verified extended indicator (`AROOND`) validates through `schema.py`, compiles
via `make_rule_strategy`, and executes 21 real trades through `run_backtest`,
with `result.extended_indicators_used == ['AROOND']` — proving the registry
swap in `schema.py`/`rule_strategy.py` (section 2) actually closes the gap it
was meant to close, not just that it imports without error.

The new formal test suite — `test_indicator_core.py` (89 tests, every core
indicator at both its min and max declared bounds), `test_schema.py` (26 tests,
including every listed `ValidationError` case from the plan and the extended-tier
smoke test), and `test_extended_indicators.py` (2 tests, the adversarial case
checked in both directions) — brought the full suite from 17 tests to 133, all
passing. The adversarial test specifically proves the verifier isn't just
reflexively rejecting everything: it rejects a genuinely dead parameter
(`cksp`'s `q`, independently confirmed dead by this component's own exploration)
**and** accepts a genuinely valid indicator (`aroon`'s `length`/`scalar`, both
independently confirmed sensitive) — the same true-positive/true-negative
standard the Stage 3 gate's morning-star case uses, and the standard the
Component 5 dedup regression test needed a second pass to actually meet.

**What this does not prove.** The 66 rejected entries were rejected correctly
*by this component's own checks*, but no one has individually gone back and
asked, for each one, whether a smarter check (a different baseline, a longer
synthetic dataset, a different sensitivity-test strategy) might have rescued
some of them — the conservative default (reject on any doubt) was chosen
deliberately, consistent with "no unverified claim enters the registry," but it
means the true usable set is a lower bound, not necessarily the ceiling. The
`SIGTRAP` crash, discussed above, was worked around (by excluding the one
function whose presence coincided with it) rather than root-caused — if the
real trigger is something environment-specific (this machine's numba/LLVM
build) rather than `ichimoku` itself, it could in principle recur under a
different set of circumstances the next time this generation pipeline is
re-run, e.g. after a pandas-ta or numba version upgrade. And the synthetic data
used throughout is one fixed random seed, at one fixed volatility profile — it
is not real market data, and nothing in this component claims the *values* an
extended indicator produces are financially meaningful, only that the function
executes correctly and its parameters do what they claim to.

---

## 6. Interview defense

**Q: Why generate and verify as two separate passes instead of proposing
bounds and immediately using them?**

A: Because "the LLM said so" and "this was checked by execution" need to stay
distinguishable, and `verified: bool` is the thing `schema.py`'s validator
actually enforces before a rule can use an indicator. Collapsing the two steps
would mean either trusting the LLM's claim outright (exactly the fabrication
risk this whole project's rigor rules exist to prevent) or building the checks
inline in a way that can't be re-run independently — for instance after a
pandas-ta version upgrade — without re-spending LLM calls.

**Q: Why let an LLM propose anything at all here — doesn't `CLAUDE.md` say
vagueness stops at the human boundary, and Stages 1–3 use no LLM?**

A: This is a deliberately narrow, disclosed exception, not a quiet violation.
`docs/architecture.md` and `CLAUDE.md` were both explicitly amended, before this
component was built, to record it: the LLM proposes exactly one thing — numeric
`(min, max)` bounds and cross-parameter ordering constraints, the one piece of
information genuinely not derivable from a function's signature by
introspection alone — and every single proposal is executed and checked before
it can be used. It runs offline, once, at build time; nothing in the live
trading-research agent (Stage 5, not yet built) depends on this call happening
again. If the LLM proposed a nonsensical bound, or a cross-check that isn't
real, the verify script's job is specifically to catch that, not to trust it.

**Q (hard): Why should anyone trust the *values* an extended indicator
produces, given that a language model chose the range of parameters it can be
configured with?**

A: They shouldn't trust the values for anything financial yet — and this
component doesn't claim they should. What's actually verified is narrower and
more honest: the function executes without error across its declared range, its
parameters are not dead (each one demonstrably changes the output), and any
claimed ordering constraint between parameters is real, not asserted. None of
that is a claim about whether, say, a particular `length` setting produces a
*good* trading signal — that's an empirical question for the backtester and the
Stage 5 research agent to answer later, the same way it already is for every
core indicator. The honest boundary of what this component proves is: "this
indicator can be safely wired into a rule and will behave the way its
parameters claim to," not "this indicator is a good idea."

**Q: You had an unexplained process crash (`SIGTRAP`) mid-development. Why
ship this component without root-causing it?**

A: Because the fix that stopped it (excluding `ichimoku`'s incompatible
`tuple` return type) was independently correct on its own merits — that return
shape genuinely can't be expressed by this registry regardless of any crash —
and after that fix, extensive re-testing (the full 193-candidate run, twice)
never reproduced the crash again. Root-causing a non-reproducing native-level
issue with the tools available in this session (no debugger attached to a
crashed interpreter, no way to inspect numba's internal JIT state after the
fact) would have cost real time for a fix that might not even be the actual
one. The honest record — stated plainly here, not hidden — is that the precise
mechanism remains unconfirmed; the residual risk is called out explicitly in
section 5 rather than glossed over.

**Honest weakness:** the 66 rejected entries are a conservative lower bound,
not a ceiling — some might be rescuable with a smarter check, and no one has
gone back to check which. Anyone extending this pipeline later should read that
as "there's headroom here," not "these are all definitively impossible."

---

## 7. What comes next and why

Immediately outstanding, per `docs/plans/stage-3-plan.md`: the dedicated plan §8
test-suite pass (`test_evaluator.py`, the full `test_rule_strategy.py`
expansion, and `test_extended_indicators.py`'s remaining structural-validity
cases), the Stage 3 gate script (`scripts/verify_stage3_gate.py` — literature
strategies plus morning star on real AAPL data), the Alembic baseline (deferred
to stage close, now due), and the doc updates recording that Stage 3 uses the
LLM in this one bounded, offline sense (§11 of the plan, and the corresponding
`CLAUDE.md`/`docs/architecture.md` amendment this explainer's own section 1
already describes but which the source documents themselves still need edited
to match).

If this component were subtly wrong in a way nobody caught, the most likely
failure mode, given the "verified means checked by execution" contract, is a
loud failure the next time someone tries to *use* a wrongly-verified indicator
in a rule — a spurious crash or an obviously nonsensical backtest result — not
a silently wrong number quietly shaping a verdict. That's the preferable
failure mode by this project's own standard. The sharper risk is upstream of
that: if the *verification checks themselves* have a blind spot (for instance,
a dead parameter that happens to affect the output only in some regime the
1500-bar synthetic dataset never exercises), an indicator could pass
verification and still be less trustworthy than its `verified=True` flag
implies. Nothing in this component's testing rules that out — it's the natural
next thing to interrogate if Stage 5's research agent later shows a
suspiciously distinctive pattern of results whenever a particular extended
indicator is involved.
