# Step 4 — Indicators Tool (Stage 4)

## 1. What this does

This component adds two MCP tools: `compute_indicator(ticker, name, params,
start, end) -> list[IndicatorValueOut]`, which computes any one registered
indicator's full time series for a ticker, and `list_indicators() ->
list[IndicatorInfo]`, which dumps the registry's own metadata — every
indicator's name, tier, verification status, required inputs, and parameter
bounds. Together they make Stage 3's two-tier indicator registry
(`ALL_INDICATORS`, 222 entries: 29 hand-verified core + 193 auto-verified
extended) discoverable and directly usable from outside a `StrategyRule`,
for the first time.

This is a meaningfully different kind of component from Components 2 and 3.
Both of those wrapped an existing function completely unchanged — Component
2 called `load_price_data` as-is, Component 3 called `run_backtest` and
`make_rule_strategy` as-is. Nothing in the codebase, before this component,
computed a single indicator's values standalone. `rule_strategy.py`
computes indicator series too, but only ever wired through
`backtesting.py`'s own `self.I()` call, inside a running `Strategy`
instance's `init()` — there was no path to "just give me `SMA(10)` for
AAPL" without first compiling an entire strategy around it. This component
is the first place in Stage 4 that had to write real new domain logic, not
just plumbing.

What this component is *not*: it does not add any indicator that wasn't
already in Stage 3's registry, and it does not change how any indicator is
computed. The exact same `IndicatorSpec.fn`, the exact same
`normalize_params`, the exact same `select_output_column` that
`rule_strategy.py` already used are reused here, unmodified — this
component only adds a new way to *reach* that existing computation, outside
the context of a compiled `Strategy`.

---

## 2. Every meaningful line explained

### `src/backtester/indicator_compute.py`

```python
_FIELD_TO_COLUMN = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
```

Maps the lowercase field names `IndicatorSpec.inputs` uses (e.g.
`("close",)` for `SMA`, `("high", "low", "close")` for `ATR`) to the
capitalized column names `load_price_data`'s returned DataFrame actually
uses (`Open`, `High`, `Low`, `Close`, `Volume`). This is a near-exact copy
of `rule_strategy.py`'s own `_FIELD_TO_ATTR` — why it's a copy rather than a
shared import is its own decision, covered in section 3.

```python
def compute_indicator(
    ticker: str,
    name: str,
    params: dict[str, float],
    session: Session,
    start: date | None = None,
    end: date | None = None,
) -> pd.Series:
```

`session: Session` is a required parameter, not something this function
opens for itself — matching `load_price_data`'s own shape exactly. Why that
matters, and why it's not a new decision so much as a continuation of one
already made twice before in this stage, is covered in section 3.

```python
    IndicatorTerm(name=name, params=params)  # validation only; result discarded
```

The single most important line in this file, and the one line whose value
comes entirely from something it does *not* do: it does not check whether
`name` exists in the registry, whether it's `verified`, whether each
parameter is within its declared bounds, or whether a `cross_check`
constraint (like MACD's fast-must-be-less-than-slow) holds — none of that
logic is written here. `IndicatorTerm` is a Pydantic model from
`schema.py`, and constructing one automatically runs its own
`_check_indicator` `model_validator`, which already does every one of those
checks, tested independently as part of Stage 3's own 170-test suite. If
`name`/`params` fail any of them, this line raises a `pydantic.ValidationError`
before the function goes any further — the result of the construction is
never even assigned to a variable, because the only thing this line is for
is triggering that validator.

```python
    spec = ALL_INDICATORS[name]
```

Safe to index directly, with no existence check of its own, specifically
*because* of the line above: if `name` weren't a real key in
`ALL_INDICATORS`, `IndicatorTerm`'s own validator would already have raised
on the previous line (its `_check_indicator` does `ALL_INDICATORS.get(name)`
and raises `unknown indicator {name!r}` if that's `None`). Two lines that
look independent are actually coupled — reordering them, or removing the
line above, would turn this into a raw `KeyError` with none of
`IndicatorTerm`'s more specific, more useful message.

```python
    df = load_price_data(ticker, session, start=start, end=end)
    price_args = [df[_FIELD_TO_COLUMN[field]] for field in spec.inputs]
    result = spec.fn(*price_args, **normalize_params(params))
```

Loads the price data, then builds the exact positional-argument list
`spec.fn` (the underlying `pandas-ta` function) expects, in the order
`spec.inputs` declares them — `ATR`'s `spec.inputs` is `("high", "low",
"close")`, so this produces `[df["High"], df["Low"], df["Close"]]` in that
order, matching `pandas_ta.atr(high, low, close, ...)`'s own positional
signature. `normalize_params(params)` is not a new function — it's Stage
3's own, imported and called exactly as `rule_strategy.py` already does,
turning whole-valued floats like `length=10.0` into real Python ints
(`10`), because some `pandas-ta` functions use a numba-jitted code path
that raises a `TypingError` on a float where an int is expected. This isn't
a theoretical risk being guarded against speculatively — Stage 3's own
`step-01-indicator-registry.md` documents this failure occurring for real
during that stage's own development, across dozens of functions, before
`normalize_params` existed to prevent it. Calling the same shared function
here, rather than writing a second version, is what keeps this component
from being able to reintroduce that exact bug on a new code path.

```python
    if result is None:
        raise ValueError(f"{name}: pandas-ta returned None — check inputs (e.g. a required DatetimeIndex)")
    return select_output_column(result, spec.column_prefix)
```

Also copied verbatim from the pattern `rule_strategy.py`'s `_compute`
closure already uses — some `pandas-ta` functions return `None` for
specific input shapes (a documented `pandas-ta` quirk, not something this
project introduced), and `select_output_column` (Stage 3's own function,
also imported unchanged) picks the single correct column out of a
multi-output indicator like `MACD` using `spec.column_prefix`, or passes a
single-output `Series` straight through unchanged when `column_prefix` is
`None`.

### `src/mcp_tools/server.py` — the two new tools

```python
from backtester.indicator_compute import compute_indicator as _compute_indicator
```

Aliased on import for the same reason Component 3 aliased `run_backtest` —
the tool function below needs the bare name `compute_indicator` for itself,
and it collides with the pure function's own name.

```python
@mcp.tool()
def compute_indicator(
    ticker: str,
    name: str,
    params: dict[str, float] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[IndicatorValueOut]:
    """Compute a registered indicator's full time series for a ticker."""
    with SessionFactory() as session:
        series = _compute_indicator(ticker, name, params or {}, session, start=start, end=end)
    return [
        IndicatorValueOut(date=idx.date(), value=float(val))
        for idx, val in series.items()
        if pd.notna(val)
    ]
```

`params: dict[str, float] | None = None` — `None`, not a bare `params:
dict[str, float] = {}` default. This isn't the same "avoid drift" reasoning
as Component 3's `commission`/`cash`; it's a different, classic Python trap:
a mutable object as a default argument value is created exactly once, at
function-definition time, and shared across every call that doesn't
override it — if this were written as `params: dict = {}` and something
later mutated that dict in place, every subsequent call omitting `params`
would see the mutation. Nothing in this specific function mutates `params`,
so the bug wouldn't actually manifest today, but `params or {}` avoids
depending on that being true forever. `params or {}` converts `None` (or,
incidentally, an empty dict — `{} or {}` is still `{}`, harmless here) into
a fresh dict at call time, then that fresh, per-call dict is what
`_compute_indicator` and `IndicatorTerm`'s validator both see.

`if pd.notna(val)` — filters out any `NaN` value in the computed series
before it's ever wrapped in an `IndicatorValueOut`. Why this lives here,
in the MCP wrapper, rather than inside `compute_indicator` itself, is
section 3's fourth decision.

```python
@mcp.tool()
def list_indicators() -> list[IndicatorInfo]:
    """Every indicator usable by compute_indicator or a StrategyRule, with tier, verification status, and parameter bounds."""
    return [
        IndicatorInfo(name=name, tier=spec.tier, verified=spec.verified, inputs=list(spec.inputs), params=spec.params)
        for name, spec in sorted(ALL_INDICATORS.items())
    ]
```

No pure-logic module backs this one — it's a direct, sorted dump of
`ALL_INDICATORS`. `sorted(ALL_INDICATORS.items())` sorts by dict key (the
indicator name) alphabetically; the registry's own natural iteration order
is core-then-extended, insertion order, not alphabetical — sorting here is
purely for making the output easier for a human (or, eventually, an agent
scanning for a specific name) to read, not a correctness requirement.
`list(spec.inputs)` converts `IndicatorSpec.inputs`'s tuple into a plain
list, matching `IndicatorInfo.inputs: list[str]`'s declared type — this
model deliberately doesn't mirror `IndicatorSpec`'s internal Python types
field-for-field (a tuple has no equivalent in JSON anyway), only the shape
that's actually useful to serialize.

### `src/mcp_tools/schemas.py`

```python
class IndicatorValueOut(BaseModel):
    date: date
    value: float

class IndicatorInfo(BaseModel):
    name: str
    tier: Literal["core", "extended"]
    verified: bool
    inputs: list[str]
    params: dict[str, tuple[float, float]]
```

Both are plain response-shaping models, no validators — the same role
`PriceBarOut` already plays. `IndicatorInfo.params`'s type,
`dict[str, tuple[float, float]]`, matches `IndicatorSpec.params`'s own type
exactly (`dict[str, tuple[float, float]]` in `indicators.py`), because
there's nothing to translate here — a `(low, high)` bounds pair is already
JSON-representable as a two-element array.

---

## 3. Design decisions and rejected alternatives

### Reusing `IndicatorTerm` for validation, without exposing its shape

The chosen approach — construct `IndicatorTerm(name=name, params=params)`
purely to trigger its existing validator, then discard it — is the same
"why write it twice" reasoning `docs/explanations/stage-4/step-03-backtester-tool.md`
already established for reusing `StrategyRule`'s validation in the
backtester tool. The rejected alternative here is identical in shape to
that component's: hand-writing the same unknown-name check, `verified`
check, per-parameter bounds check, and `cross_check` logic a second time
inside `compute_indicator`. Rejected for the same reason — that logic
already exists, is already correct, and is already covered by Stage 3's own
test suite; reimplementing it would only create a second copy that could
drift out of sync with `schema.py`'s if either one were ever changed
without remembering the other.

This component adds one refinement beyond Component 3's version of the same
pattern: `IndicatorTerm`'s full shape includes a `kind` field (always
`"indicator"` here, defaulted) and an `offset` field (meaningful only when
evaluating a term *inside* a `StrategyRule`'s bar-by-bar condition tree —
"look back `offset` bars from the current one"). Neither has any meaning
for "compute this indicator's entire series, unconditionally" — there is no
"current bar" outside a running backtest. Rather than exposing
`IndicatorTerm`'s full shape as this tool's actual parameter type — which
would mean a caller has to supply a meaningless `offset` just to satisfy
the model — `compute_indicator`'s real interface stays the plain `name:
str, params: dict[str, float]` pair, and `IndicatorTerm` is constructed and
thrown away entirely inside the function body, invisible to any caller.

**Reversibility:** fully reversible and low-risk either way — if
`IndicatorTerm`'s validator ever needs to diverge from what standalone
computation should allow, replacing this one line with hand-written checks
is a contained, single-function change.

### `_FIELD_TO_COLUMN` duplicates `rule_strategy.py`'s `_FIELD_TO_ATTR`, deliberately

This decision was discussed explicitly before any code was written, not
decided unilaterally. The alternative seriously considered — pulling this
five-entry mapping into `indicators.py` as a shared, public constant and
updating `rule_strategy.py` to import it too, eliminating the duplication
entirely — was rejected, on a distinction worth stating precisely because
it looks, at first glance, like it contradicts Component 3's own
"don't duplicate `commission`/`cash`" decision.

The two cases are not actually the same kind of duplication.
`_DEFAULT_COMMISSION`/`_DEFAULT_CASH` in `engine.py` are business-decision
constants — numbers someone could reasonably change (a different default
commission rate, say), and if they did, every duplicated copy elsewhere in
the codebase would silently keep using the old value unless someone
remembered to update it too. That's real drift risk, and it's why
Component 3 deliberately avoided copying those literals. `_FIELD_TO_COLUMN`
is a different kind of thing entirely: it's a fixed mapping onto
`backtesting.py`'s own column-naming convention, a convention this project
has no control over and no reason to ever change independently. There is no
plausible future where `_FIELD_TO_COLUMN`'s mapping needs to diverge from
`_FIELD_TO_ATTR`'s, because both exist to describe the same fixed external
fact. Duplicating five stable key-value pairs was judged lower-risk than
modifying `rule_strategy.py` — already-tested, working Stage 3 code — for a
refactor whose benefit would be purely cosmetic.

**What it would cost to reverse this decision:** trivial either way. If a
seventh or eighth place in the codebase ever needs this same mapping, that
would be a much stronger signal that a shared constant is actually earning
its keep, and the refactor becomes easy to justify then, on real repeated
evidence rather than a two-instance coincidence.

### Session ownership: the pure function takes a session, doesn't open one

`compute_indicator` (the pure function in `backtester/`) requires `session`
as a parameter and never constructs its own; `server.py`'s MCP wrapper
opens it via `SessionFactory()` and passes it in. This is not a new
decision invented for this component — it's the exact division of
responsibility Components 2 and 3 both already established (`get_price_data`
and `run_backtest` both own their own session lifecycle in `server.py`,
calling functions that accept an already-open session). It's worth naming
explicitly here anyway, because this is the first component whose *new*
pure function had to make this choice for itself, rather than simply
reusing a Stage 2/3 function that had already made it. The alternative —
letting `compute_indicator` open and close its own session internally —
was never seriously considered, because it would have made the function
untestable without either a real database or a mocked `SessionFactory`,
and inconsistent with `load_price_data`'s own shape, which this function
calls directly.

### Filtering `NaN` values in the MCP wrapper, not the pure function

`compute_indicator` (the pure function) returns the real, complete
`pandas.Series` `spec.fn` produces — including any leading `NaN` values,
e.g. `SMA(10)`'s first 9 bars, before there are enough prior closes to
compute a rolling average. The MCP tool wrapper in `server.py` is what
filters those out, via `if pd.notna(val)`, before building any
`IndicatorValueOut`.

The rejected alternative was filtering inside the pure function itself, or
alternatively returning `NaN` values as-is and letting the MCP layer's JSON
serialization deal with them. The second option was ruled out first and
more firmly: standard JSON has no `NaN` token at all; Python's `json`
module will emit a non-standard literal `NaN` by default, which many
strict JSON parsers — potentially including whatever eventually consumes
this tool's output — will reject outright. Filtering was chosen over
substituting `null`, to match a pattern already established elsewhere in
this project's design: disclosing "no value exists yet" by omitting the
row entirely, rather than forcing a placeholder into a row that doesn't
really have valid data (the same instinct behind the still-unbuilt regime
classifier's planned "insufficient history" marker in the approved Stage 4
plan).

Placing the filter in the *wrapper* rather than the *pure function* was the
more deliberate half of this decision: "what should happen to a `NaN` value
in a JSON response" is a fact about MCP's response format, not a fact about
what `SMA(10)` actually computed. A future non-MCP caller of
`compute_indicator` — Component 6's statistics tool is a plausible
candidate, if it ever needs a raw indicator series rather than a backtest
result — should see the complete, honest computation, `NaN`s included,
because that caller might have a legitimate reason to know exactly which
bars have no defined value yet, information the MCP-facing tool
deliberately discards for a presentation reason that has nothing to do
with that hypothetical caller's needs.

---

## 4. Concepts introduced

**A validation error raised from application code, not the SDK's own
argument-parsing layer, and what that reveals about the error-conversion
mechanism.** Every error path verified so far in this stage — Component 2's
unknown ticker, Component 3's malformed `StrategyRule` — was either a plain
`ValueError` from deep inside Stage 2/3 code, or a `ValidationError` raised
by the SDK's *own* machinery while building a tool's arguments from raw
JSON (`StrategyRule` was itself the typed parameter in Component 3, so the
SDK constructed and validated it before the tool body ran at all). This
component's error path is structurally different: `compute_indicator`'s
actual MCP-facing parameters are plain, un-nested types (`name: str,
params: dict`), so nothing the SDK's own argument layer does can catch a
bad indicator name — the `pydantic.ValidationError` here comes from
ordinary application code, several function calls deep, raised by
`IndicatorTerm`'s constructor running inside `compute_indicator`'s own body.
Confirming this still converts to a clean `is_error=True` result — verified
directly, not assumed by analogy to the earlier cases — is meaningful
evidence about *why* the mechanism works at all: `_handle_call_tool`'s
catch-all only checks whether an exception is an `MCPError` subclass, a
fact about the exception's *type*, completely independent of *where in the
call stack* it was raised. That's a stronger, more general claim than "the
SDK's own validation errors get caught" — it's "any exception this project's
own code raises, anywhere beneath a tool function, gets caught the same
way," which is closer to what Component 9's eventual verification actually
needs to be true across all six tools, most of whose validation logic (like
this one) lives inside application code rather than at the SDK's outer
layer.

---

## 5. How this component was tested

Continuing the same escalating pattern established in every prior
component this stage: real calls through `_handle_call_tool` (the actual
protocol path), against the real `strategy_research` database, both happy
paths and error paths, no synthetic stand-ins once the real tools existed.

**`list_indicators()`**: returned 222 total entries — 29 `tier="core"` +
193 `tier="extended"`. The core count is a genuine cross-check, not a
coincidence: it matches Stage 3's own documented "29 hand-verified"
core-registry count exactly (`docs/explanations/stage-3/stage-3-summary.md`),
confirming this tool is reading the same registry Stage 3 actually built
and verified, not a stale or parallel copy. `SMA`'s entry came back exactly
as expected — `tier: "core"`, `verified: true`, `inputs: ["close"]`,
`params: {"length": [2.0, 200.0]}` — and a sampled extended entry
(`ABER_ATR`) correctly showed `verified: false`, confirming the two-tier
`verified` flag that gates what `schema.py` will accept in a `StrategyRule`
is visible end-to-end through this tool too.

**`compute_indicator(ticker="AAPL", name="SMA", params={"length": 10},
start="2024-01-01", end="2024-02-15")`**: returned 23 rows, the first dated
2024-01-16 — exactly 9 trading days after the range's start (2024-01-02),
which is precisely `SMA(10)`'s warm-up period (9 prior closes needed before
the 10th bar has a valid 10-bar average). This is a real, checkable number,
not just "the call didn't crash" — it directly confirms the `NaN`-filtering
removed exactly the expected leading bars, no more and no fewer.

**Error path 1**, unknown indicator name (`"NOTREAL"`): `is_error=True`,
content `"Error executing tool compute_indicator: 1 validation error for
IndicatorTerm\n  Value error, unknown indicator 'NOTREAL'..."`.

**Error path 2**, out-of-bounds parameter (`SMA` with `length=99999`
against a declared bound of `[2, 200]`): `is_error=True`, content
`"...Value error, SMA.length=99999.0 out of bounds [2, 200]..."`.

Both error messages read `"1 validation error for IndicatorTerm"` — not
`"...for compute_indicatorArguments"`, the shape Component 3's `StrategyRule`
error used. That difference is expected and confirms, rather than
contradicts, this component's own design: the validation is happening
inside application code (`IndicatorTerm`'s constructor, called from within
`compute_indicator`'s body), not at the SDK's outer argument-coercion
boundary, exactly as designed in section 3's first decision.

Full existing 170-test suite run and confirmed unchanged, both immediately
before this component's code was written and immediately after.

**What this does not prove.** No automated, committed test exists yet for
either tool, consistent with every component so far this stage — Component
8 is still where formal, repeatable coverage lands. It also doesn't prove
anything about an indicator with a `cross_check` constraint (e.g. `MACD`'s
fast-must-be-less-than-slow) actually being rejected through this specific
path — the out-of-bounds test exercised the per-parameter bounds check, not
the cross-parameter one, a real, disclosed gap rather than a claim this
component didn't actually verify. Nor does it prove anything about an
*extended*-tier indicator being computed successfully through this tool —
every live check here used `SMA`, a core indicator; an extended indicator's
`fn` (verified by Stage 3's own execution-based pipeline, but with
different code paths, e.g. multi-output DataFrames needing `column_prefix`
selection) was only exercised by `list_indicators()`'s metadata dump, never
actually computed through `compute_indicator` in this component's
verification.

---

## 6. Interview defense

**Q: Why does this component need a new file (`indicator_compute.py`) at
all — why not just put `compute_indicator`'s logic directly inside the MCP
tool function in `server.py`?**

A: Because the approved Stage 4 plan's own architecture — domain logic
lives in the package that owns the domain, `mcp_tools/` holds only thin
MCP-facing adapters — already answered this before this component started,
and Components 2 and 3 already established the pattern for functions that
pre-existed. This component is the first case where the *domain logic
itself* is new, so it had to actually choose where that logic lives, not
just wrap something already living somewhere. Putting it in `backtester/`
means it's testable independent of MCP entirely (a future Component 8 test
can call `compute_indicator` directly with a mocked session, no MCP
machinery involved at all), and it means a future non-MCP caller — the
statistics tool is a real, concrete future candidate — can reuse it without
importing anything from `mcp_tools/`.

**Q: `list_indicators()` returns 222 indicators, only 29 of them
hand-verified. Doesn't exposing 193 auto-verified, execution-checked-but-
never-hand-reviewed extended indicators to a tool an agent will eventually
call carry real risk?**

A: The risk is real but it's Stage 3's risk, already taken and already
disclosed, not a new one this component introduces. `verified: bool` on
every `IndicatorInfo` entry is exactly the signal Stage 3 built for this —
`schema.py`'s `IndicatorTerm._check_indicator` already refuses any
indicator with `verified=False` unconditionally, so an unverified extended
indicator (there are some, `ABER_ATR` sampled in this component's own
verification among them) can be *listed* by `list_indicators()` but cannot
actually be *used* by `compute_indicator` or a `StrategyRule` — both go
through the identical `IndicatorTerm` validation gate. Exposing the
unverified ones in the listing, rather than filtering them out, was a
deliberate choice for a reason worth stating: an agent (or a human) that
can see "here are 193 candidates, only some verified" has more useful
information than one that only ever sees the 29 already-cleared ones with
no visibility into what exists but isn't usable yet — visibility into the
gap is more honest than hiding it.

**Q (hard): This component's own verification section admits it never
tested a `cross_check` rejection or ever successfully computed an
*extended*-tier indicator through `compute_indicator` — only through
`list_indicators()`'s metadata dump. Given this project's stated
"verify by execution, never trust a claim" discipline, isn't shipping this
component with those two specific gaps a direct contradiction of that
discipline, not just an incidental oversight?**

A: It's a real gap, correctly flagged rather than smoothed over, but it's
not the same failure the discipline exists to prevent. That discipline is
about not *asserting* something is true without having checked it — this
component doesn't assert extended-indicator computation or cross-check
rejection work through this path; section 5 states plainly that neither
was tested. The actual risk of leaving this gap open is bounded, not
unknown: `compute_indicator`'s `IndicatorTerm(name=name, params=params)`
validation line is the *exact same* validator `schema.py`'s own test suite
already exercises for cross-check rejection (Stage 3's tests cover
`_apply_cross_check` directly), and extended-indicator execution through
`spec.fn` is the exact same call `rule_strategy.py` already makes and
Stage 3's `generate_extended_indicators.py`/`verify_extended_indicators.py`
pipeline already proved works for every `verified=True` entry. What's
genuinely untested is narrower than "does this work at all" — it's "does
this specific new code path (`compute_indicator`, not `rule_strategy.py`)
correctly reach that already-proven machinery for these two particular
cases." That's exactly the kind of gap Component 8's formal test suite
exists to close, and naming it now, rather than discovering it silently
later, is the discipline actually working as intended — not a violation of
it.

**Honest weaknesses, stated plainly:** no automated test exists for either
tool yet (true of every component this stage so far). The `cross_check`
and extended-indicator-execution gaps named above are real and specific,
not vague hedging. And `list_indicators()`'s alphabetical sort is a
readability choice with no test verifying it — a future change to
`ALL_INDICATORS`'s construction that accidentally broke the sort would only
be caught by a human noticing the output looks wrong, not by anything
automated.

---

## 7. What comes next and why

Component 5 (regime classifier) is a return to the shape Component 4 just
established — new domain logic, not a pure wrapper — but with a
structurally different risk this component didn't have to face: Component
4's indicator values are computed independently at each bar (an `SMA`
value at bar 500 doesn't depend on where in the query range bar 500 falls),
so there was no lookahead question to resolve here. Component 5's regime
labels are explicitly *relative* — a quantile computed against a rolling
history — which means the exact question this component didn't need to ask
("could this value at bar N depend on data that wouldn't have existed
yet, if this were computed live") becomes the central design question next.

If this component's core assumption — that `IndicatorTerm`'s validator is a
safe, complete substitute for hand-written checks — turns out wrong for some
case not yet exercised (the `cross_check` gap named above being the most
concrete candidate), the most likely symptom is a false negative, not a
silently wrong number: a `StrategyRule` that `schema.py` would reject stays
rejected either way, since both paths converge on the identical validator;
what could actually go wrong is `compute_indicator` accepting a parameter
combination this component's own verification never actually tried,
producing a technically-valid-looking series from `spec.fn` that a human
or future agent would have no independent reason to distrust. That's a
real, if narrow, residual risk this component's honest gaps leave open
until Component 8 closes it.
