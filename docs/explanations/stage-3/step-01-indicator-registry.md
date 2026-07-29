# Step 1 — Indicator Registry (Stage 3)

## 1. What this does

`src/backtester/indicators.py` is a catalogue of every technical indicator the
system is allowed to use in a strategy rule. For each one it records: which
pandas-ta function computes it, which price columns (open/high/low/close/volume)
that function needs, what parameters are tunable and within what bounds, and — for
indicators that return more than one output — which output column belongs to this
entry.

It is explicitly **not** a computation layer. It does not run indicators, evaluate
conditions, or touch backtesting.py. Nothing in this file executes a strategy. The
rule interpreter that will consume this registry (`strategies/rule_strategy.py`,
Stage 3 Component 4, not yet built) is the thing that actually calls these
functions during a backtest. This file only answers two questions: *what indicators
exist*, and *how do I legally call each one*. Keeping those questions separate from
"how do I run a backtest" is what makes the registry testable in isolation — you
can verify every entry executes and returns sane output without a Strategy object,
a Backtest object, or any price data beyond a synthetic DataFrame.

The registry has two tiers, though only the `core` tier is built in this step:

- **`core`** — 29 entries, hand-picked and hand-verified by executing them against
  pandas-ta 0.4.71b0 (this file's `CORE_INDICATORS` dict).
- **`extended`** — the remainder of pandas-ta's indicator catalogue, generated
  offline by an LLM proposing parameter bounds and then verified by execution
  (Stage 3 Component 7, not yet built). Extended entries start `verified=False` and
  stay unusable until a verification script flips them.

---

## 2. Every meaningful line explained

### `MAX_LOOKBACK = 5`

```python
MAX_LOOKBACK = 5
```

A single named constant for how many bars back a rule is allowed to reference
(`offset=-1` means "yesterday", `offset=-5` is the deepest allowed look-back). It
lives here, not in `schema.py`, because it is a property of what the registry
promises to support — deeper offsets would require every indicator's warm-up
behavior to be re-checked. The schema validator (Component 3) imports this constant
rather than redeclaring `5` itself, so there is exactly one place to change if the
limit ever moves. Positive offsets are not a "high" value of this constant — they
are rejected outright, because a positive offset means "look at a future bar,"
which is a lookahead violation. This file just defines the ceiling on how far back
"backward" is allowed to reach; the schema layer (Component 3) is what actually
raises on positive offsets.

### `_PRICE_FIELDS` and `_infer_inputs`

```python
_PRICE_FIELDS = ("open", "high", "low", "close", "volume")

def _infer_inputs(fn: Callable) -> tuple[str, ...]:
    sig = inspect.signature(fn)
    return tuple(
        name
        for name, param in sig.parameters.items()
        if name in _PRICE_FIELDS and param.default is inspect.Parameter.empty
    )
```

This function answers "which price columns does this pandas-ta function need?" by
reading the function's actual Python signature with `inspect.signature`, rather
than by a human writing `inputs=("high", "low", "close")` by hand for each entry.

Two conditions must both hold for a parameter to count as a required input:

1. **Its name is one of the five OHLCV fields.** This filters out non-price
   parameters like `length`, `talib`, or `mamode`.
2. **It has no default value** (`param.default is inspect.Parameter.empty`). This
   is what correctly excludes `ta.ad`'s `open_` parameter: `ad` accepts an optional
   `open_` argument for a variant of the Accumulation/Distribution formula, but it
   defaults to `None` and the plain A/D calculation doesn't need it. Signature
   introspection alone (just checking the name) would have kept the field question
   open — is `open_` required or not? Checking `.default` too answers it correctly:
   optional means excluded, so `AD` ends up needing only `(high, low, close,
   volume)`.

The order of the returned tuple matches the order pandas-ta declares its
parameters in — `inspect.signature` preserves declaration order — which matters
because the rule interpreter will eventually call `fn(*[price[f] for f in
spec.inputs])`, and positional order has to match what the function expects.

### `IndicatorSpec`

```python
@dataclass(frozen=True)
class IndicatorSpec:
    fn: Callable
    inputs: tuple[str, ...]
    params: dict[str, tuple[float, float]] = field(default_factory=dict)
    column_prefix: str | None = None
    cross_check: dict | None = None
    tier: Literal["core", "extended"] = "core"
    verified: bool = True
    verified_on: date | None = None
    lib_version: str | None = None
```

`frozen=True` makes instances immutable after construction. There is no reason
anything downstream should ever mutate a registry entry — if the interpreter or
the schema validator could reassign `spec.verified = True` at runtime, "verified"
would stop meaning anything, since verification is supposed to happen once, offline,
by actually executing the function.

`fn: Callable` stores a direct reference to the pandas-ta function object (e.g.
`ta.rsi`), not its name as a string. This rules out an entire class of bugs: no
`getattr(ta, name)` lookup that could silently return `None` or the wrong attribute
if a name were misspelled, and no risk of a string being passed to `eval`.

`params: dict[str, tuple[float, float]] = field(default_factory=dict)` — a mutable
default (`{}`) cannot be written directly as `params: dict = {}` on a dataclass,
because Python would then share **one** dict object across every instance that
doesn't override it — mutating one entry's params would silently mutate all of
them. `field(default_factory=dict)` tells the dataclass machinery to call `dict()`
fresh for each instance instead.

`column_prefix: str | None` exists only for indicators whose pandas-ta function
returns a DataFrame instead of a single Series (MACD, STOCH, BBands, ADX). More on
this below.

`verified: bool = True` — core entries default to `True` because they were tested
during this step (see section 5). Extended entries will be constructed with
`verified=False` explicitly, and the schema validator (Component 3) is what will
enforce that an unverified indicator can never appear in an executable rule.

`verified_on` and `lib_version` are populated for the extended tier's automated
verification script — recording *when* and *against which pandas-ta version* an
extended entry passed its checks. Core entries leave these `None`; the "when" and
"against which version" for the core tier is this document and this git commit,
not a field on the object.

### `_core()`

```python
def _core(
    fn: Callable,
    params: dict[str, tuple[float, float]] | None = None,
    column_prefix: str | None = None,
    cross_check: dict | None = None,
) -> IndicatorSpec:
    return IndicatorSpec(
        fn=fn,
        inputs=_infer_inputs(fn),
        params=params or {},
        column_prefix=column_prefix,
        cross_check=cross_check,
        tier="core",
        verified=True,
    )
```

A small factory so every core entry is built through one path instead of
constructing `IndicatorSpec(...)` directly 29 times with `tier="core", verified=True,
inputs=_infer_inputs(...)` repeated at every call site. If `tier` or `verified`
needs to change for the whole core set at once (unlikely, but this is the kind of
thing that saves a 29-line diff later), there is one function to edit.

### `CORE_INDICATORS`

```python
CORE_INDICATORS: dict[str, IndicatorSpec] = {
    "SMA": _core(ta.sma, params={"length": (2, 200)}),
    ...
}
```

A plain `dict[str, IndicatorSpec]` keyed by the name a strategy rule will use to
reference the indicator (e.g. `IndicatorTerm(name="RSI", params={"length": 14})` in
Stage 3 Component 3's schema). A dict rather than a list-plus-lookup-function
because the access pattern is always "give me the spec for this name" — `O(1)`
lookup with no need to scan.

### Why multi-output indicators get one registry entry per output column

`ta.macd(close, fast=12, slow=26, signal=9)` does not return a single value — it
returns a DataFrame with three columns: the MACD line, the signal line, and the
histogram. A strategy rule that says "buy when MACD crosses above its signal line"
needs to reference both of those as independent indicator values, in the same
condition, at the same time.

If the registry had one `"MACD"` entry that somehow tried to represent all three
outputs, a rule would need some extra mechanism to say "I mean the signal-line
output of this entry, not the histogram" — mixing "which indicator" with "which
column of its output" into one lookup. Instead, `MACD`, `MACD_SIGNAL`, and
`MACD_HISTOGRAM` are three separate top-level names in `CORE_INDICATORS`, all
pointing at the same `ta.macd` function, distinguished only by `column_prefix`:

```python
"MACD":           column_prefix="MACD_",
"MACD_SIGNAL":    column_prefix="MACDs_",
"MACD_HISTOGRAM": column_prefix="MACDh_",
```

A rule references `MACD_SIGNAL` exactly like it references `RSI` — as one flat
name. The interpreter, when it eventually builds this, calls `ta.macd(close, fast,
slow, signal)` once (see the deduplication note in section 3), gets back a
DataFrame, and picks the column whose name starts with `"MACDs_"`. The same
pattern applies to `STOCH_K`/`STOCH_D` (from `ta.stoch`), `BB_LOWER`/`BB_MID`/
`BB_UPPER` (from `ta.bbands`), and `ADX`/`DMP`/`DMN` (from `ta.adx`) — 4 underlying
functions producing 12 registry entries between them, plus 17 single-output
entries, for 29 total.

### Why prefix matching instead of predicting the exact column name

pandas-ta names DataFrame columns by embedding the parameters used, e.g.
`MACD_12_26_9` or `BBL_20_1.0_1.0`. You could try to predict the exact name given
the params a rule supplies (`f"MACD_{fast}_{slow}_{signal}"`), but this is fragile:
the float formatting is irregular (`1.0` not `1`, and pandas-ta's own float
rendering has changed across versions), so a hand-built format string can silently
produce a name that doesn't exist and crash — or worse, doesn't crash but selects
`None`. Matching by prefix (`col.startswith("BBL_")`) sidesteps needing to know the
exact suffix at all. It was verified during this step's testing (section 5) that a
4-character prefix like `"BBL_"` always selects exactly one column, never zero,
never more than one, across every parameter combination that was tested.

---

## 3. Design decisions and rejected alternatives

### Deriving `inputs` by introspection instead of hand-typing them

The alternative is what the empirical findings table in the Stage 3 plan already
did once, informally: manually read pandas-ta's docs or source and write
`inputs=("high", "low", "close")` for each function. This works, but it is a
hand-maintained fact about a third-party library's API, and hand-maintained facts
about external APIs drift silently. If a future pandas-ta release added a required
parameter to `ta.rsi`, a hardcoded `inputs=("close",)` would keep compiling and
keep looking correct, and would only fail at the exact moment the interpreter
called `ta.rsi(close)` and got a `TypeError` for a missing argument — likely deep
inside a backtest run, far from the actual cause.

`_infer_inputs` reads the signature at import time, every time the module loads.
If pandas-ta's signature changed in a way that mattered, the *inferred* tuple would
change immediately, and any test relying on inputs being `("close",)` would fail
loudly at the registry level — long before a backtest ever ran. The cost of this
approach is that it only works because pandas-ta's parameter *names* reliably
match the OHLCV vocabulary (`open`, `high`, `low`, `close`, `volume`) or are
optional with sensible defaults (`open_`). If some future indicator used
non-standard names for required price inputs, introspection would silently infer
an empty or wrong `inputs` tuple. That failure mode was checked for directly — see
the verification method in section 5, which executes every entry, not just trusts
the inferred inputs.

### Not declaring `std` as a bbands parameter (the plan's original finding) — corrected during this step

The Stage 3 plan's empirical findings section states that bbands has no working
`std` parameter in this pandas-ta version, and that `std=1.0` vs `std=3.0` produce
identical output — silently absorbed via `**kwargs`. That specific claim is true:
`ta.bbands(close, std=1.0)` and `ta.bbands(close, std=3.0)` really do produce
identical output, because pandas-ta accepts any keyword argument without
validating it exists, and `std` is not a real parameter name in this version.

But re-testing it during this step, guided by the same "introspect the signature,
don't guess" principle this file already applies to `inputs`, found that `ta.bbands`
does have real, working standard-deviation controls — they are just named
`lower_std` and `upper_std`, not `std`:

```
ta.bbands(close, length=20, lower_std=1.0, upper_std=1.0)
ta.bbands(close, length=20, lower_std=3.0, upper_std=3.0)
```

produce genuinely different output (verified column-by-column, not just "it ran
without error"). The original finding tested the wrong keyword name and concluded
the feature was absent; it was actually just misnamed. Registering `lower_std` and
`upper_std` gives the strategy schema real control over Bollinger Band width — a
rule can express "tight bands" vs "wide bands" — where following the plan literally
would have left that capability out entirely.

This was surfaced to the user as an explicit deviation from the written plan before
being applied, per the project's working agreement that plan deviations get raised
rather than silently made. The user chose to register the real parameters rather
than match the plan's original (mistaken) empirical claim.

### `d` bound on `STOCH_K`/`STOCH_D` raised from `(1, 50)` to `(2, 50)`

The Stage 3 plan does not mention this bound specifically; it was discovered while
verifying the registry (see section 5). `ta.stoch(high, low, close, k=..., d=1,
smooth_k=...)` raises `ValueError: Length of values (0) does not match length of
index (...)` regardless of what `k` or `smooth_k` are set to. This was isolated by
testing `d` alone against fixed `k=14, smooth_k=3` and confirming `d=1` fails while
`d=2` and `d=3` succeed — a genuine bug in this pandas-ta version's internal
smoothing logic at `d=1`, not a data-length problem (it reproduces on 900 bars, not
just short synthetic data).

The alternative to changing the bound would be leaving `d`'s minimum at `1` and
relying on the schema validator or the interpreter to catch the resulting exception
at rule-execution time. That would work, but it means a rule that looks
schema-valid (params in bounds) can still blow up deep inside a backtest — exactly
the "it didn't raise at declaration time" trap the plan is designed to avoid for
`bbands`. Since the registry's own bounds are what the schema validator checks
against, fixing the bound here means an invalid `d=1` rule is rejected immediately,
at validation time, with a clear "out of range" error — not three layers later as
an opaque pandas-ta stack trace.

### `VWAP` registered with no special handling, but flagged as a forward risk

`ta.vwap` is registered in `CORE_INDICATORS` the same way every other volume
indicator is — no params, inputs inferred as `(high, low, close, volume)`. But
during verification it was discovered that `ta.vwap` requires its input Series to
carry a real `pandas.DatetimeIndex`; called on data with a plain integer
`RangeIndex`, it does not raise — it prints `"[!] VWAP requires an ordered
DatetimeIndex."` to stdout and returns `None`. This matters because VWAP's
definition resets its cumulative calculation at anchor points (by default, daily),
so it structurally needs to know where calendar boundaries fall; an indicator like
`RSI` has no such need and works identically regardless of what the index is.

This step does not fix anything about this, because there is nothing in
`indicators.py` to fix — the registry doesn't build any Series, it only describes
how to call `ta.vwap` once one exists. The fix belongs to whichever code
constructs the Series that gets passed to `ta.vwap`, which is Stage 3 Component 4
(`rule_strategy.py`, not yet built). That code must preserve the real
`DatetimeIndex` from `self.data.index` when it wraps a price column for VWAP —
notably, this is *not* what the existing Stage 2 `SMACrossover.init()` does: its
`self.I(lambda s: pd.Series(s).rolling(...).mean().values, close)` pattern builds a
bare, index-less `pd.Series(s)` from the raw array `backtesting.py` hands it. That
pattern is fine for SMA (which doesn't care about the index) and would silently
break VWAP (which does). This is recorded here as a known constraint on Component
4's design, not as a defect in this component — VWAP is legitimately usable, it
just has a real precondition the interpreter must satisfy that other core
indicators don't share.

---

## 4. Concepts introduced

### Signature introspection

Python lets you inspect a function's declared parameters at runtime via
`inspect.signature(fn).parameters` — a dict-like view of parameter names, their
default values, and other metadata, without calling the function. This is
different from just reading documentation or source code by eye: it is a fact the
running interpreter can check and act on. `_infer_inputs` uses this to derive
`inputs` mechanically rather than trusting a human's memory of what a function
needs. The concept matters beyond this file: it is the same idea Stage 3 Component
7 will lean on to determine the extended tier's non-parameter facts (how many
inputs, Series vs. DataFrame return) automatically, reserving the LLM for the one
thing introspection genuinely cannot answer — reasonable parameter *bounds*.

### Sensitivity testing (the bbands / stoch lesson, generalized)

A parameter "existing" in a function signature does not mean it does anything. The
project's rule is: never trust that a keyword argument works just because the call
didn't raise an exception — actually run the function at two different values of
that parameter and confirm the *output* differs. This is what caught bbands' `std`
being silently absorbed as a no-op kwarg, and it is the same check that would catch
any future indicator with a cosmetic-only parameter. Signature introspection tells
you a parameter is *real* (declared); sensitivity testing tells you it is
*functional* (changes behavior). Both are necessary; neither alone is sufficient.

### Warm-up / lookback requirements are indicator-specific, and can be nonlinear

Most of the core indicators need roughly `length` bars before producing their
first non-NaN value (an SMA of length 30 needs 30 bars). TEMA (triple exponential
moving average) does not follow this pattern — verifying `TEMA(length=200)` needed
closer to 600 bars before producing *any* non-NaN output, because TEMA internally
computes an EMA of an EMA of an EMA, roughly tripling the effective warm-up. This
is expected behavior for a triple-smoothed indicator, not a bug, but it is a fact
worth knowing before assuming "if it returns all-NaN, the params must be wrong" —
sometimes the dataset is just too short for that combination of indicator and
period.

---

## 5. How the verification gate was satisfied

This component does not have a formal automated test file yet — `tests/backtester/
test_indicator_core.py` is Stage 3 Component 8, built later, alongside the schema
and evaluator tests. What exists for this step is an interactive verification pass
run against the actual registry before it was presented as finished, in the same
spirit as what that test file will formalize:

- Every one of the 29 `CORE_INDICATORS` entries was called with its declared
  `inputs`, at **both** its minimum and maximum declared parameter bounds (or with
  no params, for the handful with none), on 900 bars of synthetic OHLCV data with a
  real `pandas.DatetimeIndex` (2020-01-01 onward, business-day frequency) — 900
  bars specifically so that long-warm-up indicators like `TEMA(length=200)` have
  enough history to produce real output, matching how the system will actually be
  used (years of daily bars, not a few hundred).
- For every entry with a `column_prefix`, the check confirmed the prefix matches
  **exactly one** output column, not zero and not more than one.
- Every entry's output was confirmed to contain at least some non-NaN values after
  warm-up — not all-NaN, which would indicate a broken call.
- A dedicated sensitivity check on `BB_LOWER` confirmed `lower_std=1.0` vs.
  `lower_std=3.0, upper_std=3.0` produce genuinely different output (not just
  different column names) — the specific check that would have caught the original
  `std` bug had it still been present.
- The full existing test suite (`pytest tests/`, 16 tests from Stages 1–2) was
  re-run after adding this file and passed unchanged, confirming no import-time
  side effect or naming collision broke anything already working.

**What this does not prove:** it does not prove the registry is *complete* — that
every indicator a future strategy might need is present (the extended tier exists
precisely because it isn't). It does not prove the `params` bounds are
*optimal* trading ranges — they are execution-safe ranges (the function runs and
produces real output), not claims about what parameter values are good strategy
choices; that determination is what a backtest is for, not the registry. It also
does not exercise the registry through the real code path a strategy will actually
use — `self.I()` inside a `backtesting.py` Strategy — because that path
(`rule_strategy.py`) does not exist yet. The VWAP `DatetimeIndex` requirement is a
known, disclosed gap precisely because of this: it was caught by testing
`ta.vwap` directly, not by testing it through the interpreter, since there is no
interpreter yet to test it through. Component 4's own step explainer will need to
show VWAP working through the real `self.I()` path before that gap can be
considered closed.

---

## 6. Interview defense

**Q: Why derive `inputs` by introspecting the function signature instead of just
writing them down — pandas-ta's docs already say what each indicator needs?**

A: Because "the docs say" and "the installed version's function actually requires"
can silently diverge — pandas-ta is under active development and its own docs have
lagged its behavior before (the `bbands` `std`/`lower_std`/`upper_std` mismatch
found in this step is a direct example of documentation-shaped assumptions being
wrong). Reading the signature at import time means the registry's `inputs` are
always consistent with whatever pandas-ta version is actually installed, and any
mismatch surfaces as an immediate, loud failure — a `TypeError` calling the
function with the wrong arguments — rather than a quiet accuracy gap that shows up
as unexplained NaNs three layers downstream.

**Q: Why one registry entry per output column for MACD/STOCH/BBands/ADX, instead of
one entry that returns a dict or lets the rule specify a column name string?**

A: Because a flat namespace of names is what the schema layer (Component 3) already
needs — a rule's `IndicatorTerm(name="MACD_SIGNAL")` looks up one string in one
dict, identical in shape to looking up `"RSI"`. Letting a rule instead say
`IndicatorTerm(name="MACD", output="signal")` would work too, but it adds a second
axis of validation (is `"signal"` a valid output for `"MACD"`?) for no real benefit
— the four multi-output indicators in the core set only have 2–5 outputs each, so
flattening them into ~12 named entries is a small, fixed cost that keeps every
other part of the system — the schema, the deduplication key in the interpreter,
the provenance tracking — working with one flat name per computed value.

**Q (hard): You corrected the plan's bbands finding and changed the STOCH `d`
bound based on your own testing. How do you know your testing is right and the
original plan's testing was wrong, rather than the other way around — couldn't you
have made the same kind of mistake?**

A: The honest answer is that "my testing" isn't a separate authority from "the
plan's testing" — both are just runs of the same pandas-ta functions, and either
could be wrong for a reason neither caught (a bug specific to this exact installed
version, a subtlety in how the synthetic test data was generated, an environment
difference). What makes the correction defensible isn't confidence, it's that the
finding is falsifiable and was stated as such: the exact calls that were run, and
their exact outputs, are in this document — `ta.bbands(close, std=1.0)` producing
identical output to `std=3.0`, versus `lower_std`/`upper_std` producing different
output; `ta.stoch(..., d=1)` raising a specific `ValueError` regardless of `k` or
`smooth_k`. Anyone with the same pandas-ta version can re-run those exact calls and
get the same answer or a different one. The finding was also not applied silently —
it was surfaced to the user as a deviation from the written plan and only committed
to code after they chose to accept it. That process — show the exact evidence,
flag the deviation, let the human decide — is the actual safeguard here, not
personal confidence that the second round of testing was more careful than the
first.

**Honest weakness:** none of this has been exercised through the real execution
path yet. Every check in section 5 calls `ta.rsi(...)` or `ta.bbands(...)` directly
on a hand-built pandas Series; none of it goes through `backtesting.py`'s `self.I()`
wrapper, which is the actual mechanism a strategy will use. It's plausible — though
not yet observed — that something about how `self.I()` passes arrays (as raw numpy
arrays, not Series, based on Stage 2's `SMACrossover`) interacts differently with
one of these functions than the direct-Series calls tested here did. The VWAP
`DatetimeIndex` finding is a concrete instance of exactly this kind of gap; there
could be others not yet surfaced. This will be closed when Component 4
(`rule_strategy.py`) runs each core indicator through the real `self.I()` path.

---

## 7. What comes next and why

Stage 3 Component 3 (`schema.py`) builds the `Term`/`Comparison`/`Condition`/
`StrategyRule` Pydantic models that let a rule reference indicators by name. Its
`IndicatorTerm` validator will check three things directly against this registry:
the name exists in `CORE_INDICATORS` (or a verified extended entry), `verified is
True`, and every supplied param is both a declared key in `spec.params` and within
its `(min, max)` bounds — plus running `spec.cross_check` through a small
interpreter for indicators like MACD where `fast < slow` must hold across two
params at once. None of that validation logic can be written until this registry
exists to validate against, which is why the registry came first.

If this registry were wrong in a way that silently accepted a broken parameter —
the kind of mistake this step specifically went looking for with the `bbands` and
`stoch` findings — the failure would not surface here. It would surface as a
strategy rule that validates cleanly, compiles into a `Strategy` subclass, and then
either crashes deep inside a backtest run (the `stoch d=1` case) or, worse, runs to
completion and produces a plausible-looking but meaningless result because a
parameter that was supposed to vary the indicator actually did nothing (the
un-corrected `bbands std` case). The second failure mode is the more dangerous one:
a rule claiming "wide Bollinger Bands, `std=3.0`" that was silently running with the
library's default width would still produce a real Sharpe ratio, a real trade
count, real-looking output — just not measuring what the rule claimed to measure.
Catching this at the registry step, before any rule can reference the broken
parameter, is cheaper than catching it after a verdict has already been written
about a strategy that was never actually tested the way it claimed to be.
