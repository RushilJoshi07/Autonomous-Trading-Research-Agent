# Step 5 — Regime Classifier Tool (Stage 4)

## 1. What this does

This component adds `classify_regime(ticker, start=None, end=None) ->
list[RegimeRecordOut]`, the fifth MCP tool in this stage and the second
(after Component 4) to introduce genuinely new domain logic rather than
wrap something Stage 2/3 already built. For every bar in a ticker's price
history, it labels how strongly the market was trending (`choppy` /
`neutral` / `trending`, from `ADX`) and how volatile it was (`low_vol` /
`neutral` / `high_vol`, from `NATR`) — both relative to that same ticker's
own trailing 252-bar history, never against a fixed, hand-picked absolute
level.

This is the architecture doc's fourth of six planned tools
(`docs/architecture.md` §5 Step 4 lists backtester, market data,
indicators, regime classifier, statistics, screener), and the first
component in this stage whose central design question is a lookahead-bias
question — the same category of question Sacred Gate 1 (Stage 2) exists to
answer for the backtester itself, now showing up one layer up, in a tool
that labels historical periods rather than simulates trades against them.

What this component is *not*: it does not decide anything about whether a
regime is "good" or "bad" for any particular strategy — that judgment, if
it's ever made, belongs to Stage 5's agent, working from this tool's
output. This tool only answers "what kind of period was this," using
exactly the same core-registry indicators (`ADX`, `NATR`) Stage 3 already
built and verified — no new indicator math exists anywhere in this
component.

---

## 2. Every meaningful line explained

### The `indicator_compute.py` refactor

```python
def compute_indicator_series(df: pd.DataFrame, name: str, params: dict[str, float]) -> pd.Series:
    """Compute one named indicator's series from an already-loaded price DataFrame."""
    IndicatorTerm(name=name, params=params)  # validation only; result discarded
    spec = ALL_INDICATORS[name]
    price_args = [df[_FIELD_TO_COLUMN[field]] for field in spec.inputs]
    result = spec.fn(*price_args, **normalize_params(params))
    if result is None:
        raise ValueError(f"{name}: pandas-ta returned None — check inputs (e.g. a required DatetimeIndex)")
    return select_output_column(result, spec.column_prefix)


def compute_indicator(ticker, name, params, session, start=None, end=None) -> pd.Series:
    """Compute one named indicator's full series for a ticker."""
    df = load_price_data(ticker, session, start=start, end=end)
    return compute_indicator_series(df, name, params)
```

Before this component, `compute_indicator` (Component 4) did two things in
one function body: load a ticker's price data, and compute an indicator
from it. This component's own logic — computing `ADX` and `NATR` from a
DataFrame it's already given, never from a ticker — only needs the second
half. Splitting them means `classify_regime` can call
`compute_indicator_series` directly, twice, without ever touching
`load_price_data` or a database session at all — matching the approved
Stage 4 plan's own stated signature for `classify_regime`, which takes
`price_data`, not a ticker. Why this specific refactor was judged worth
touching already-committed Component 4 code, when Component 4 itself
deliberately chose *not* to refactor a similar-looking duplication
(`_FIELD_TO_COLUMN`), is section 3's first decision.

`compute_indicator_series` has no leading underscore, unlike, say,
`_FIELD_TO_COLUMN` in the same file. That's deliberate: an underscore
signals "implementation detail, not meant to be imported elsewhere," and
this function is the opposite of that from the moment it's written — it's
a second, genuine public entry point into indicator computation, for
callers that already have a DataFrame in hand rather than a ticker to look
up.

### `backtester/regime.py`

```python
REGIME_LOOKBACK_BARS = 252
```

Not a new number invented for this file — this is the exact value the
approved Stage 4 plan pinned down explicitly for this component, chosen to
match the same 252-trading-day annualization convention
`backtesting.py`'s own `Return (Ann.) [%]` (already surfaced in
`BacktestResult`) already uses elsewhere in this project, so there's only
one notion of "a year" anywhere in the codebase, not two competing ones.

```python
_TREND_PARAMS = {"length": 14}  # ADX's own pandas-ta default; within its registered [2, 100] bound
_VOL_PARAMS = {"length": 14}  # NATR's own default; same bound
```

`14` isn't an arbitrary pick — it's `pandas-ta`'s own conventional default
period for both `ADX` and the `ATR` family, and it's checked against
`indicators.py`'s registered bounds (`(2, 100)` for both) rather than
assumed to be valid; passing an out-of-bounds value here would fail loudly
the moment `compute_indicator_series` constructs its internal
`IndicatorTerm`, the same validation gate every other indicator use in this
project goes through.

```python
def _tercile_label(pct: float, labels: tuple[str, str, str]) -> str:
    low, mid, high = labels
    if pct < 100 / 3:
        return low
    if pct > 200 / 3:
        return high
    return mid
```

A small, shared helper used for both the trend and volatility labels, since
both need the identical three-way split logic against different label
vocabularies (`_TREND_LABELS`, `_VOL_LABELS`). `pct` is expected in the
0–100 range (not 0–1), matching how the percentiles are actually stored
later in this file — `100 / 3` and `200 / 3` are the tercile boundaries
(≈33.3 and ≈66.7) expressed as exact fractions rather than rounded decimal
literals, avoiding a boundary bar landing on the wrong side of a
floating-point rounding artifact from a hand-typed `33.33`/`66.67`.

```python
def classify_regime(price_data: pd.DataFrame) -> pd.DataFrame:
    adx = compute_indicator_series(price_data, "ADX", _TREND_PARAMS)
    natr = compute_indicator_series(price_data, "NATR", _VOL_PARAMS)
```

Two calls into the newly-extracted shared function — this is the entire
reason that extraction happened. Without it, this would have been two
near-identical inlined copies of `compute_indicator`'s old body, one per
indicator.

```python
    adx_pct = adx.rolling(REGIME_LOOKBACK_BARS).rank(pct=True) * 100
    natr_pct = natr.rolling(REGIME_LOOKBACK_BARS).rank(pct=True) * 100
```

For each bar, `rolling(252).rank(pct=True)` computes that bar's own
percentile rank *within the preceding 252-bar window, including itself* —
`pandas`' rolling windows are trailing by default (`center=False`), so this
line never looks at a future bar to decide the current one's rank. That's
not an assumption; it's a property confirmed with a small synthetic check
before this file was written — see section 3's third decision. `* 100`
converts `rank(pct=True)`'s native 0–1 fraction into the 0–100 scale this
tool reports and `_tercile_label` expects.

```python
    trend_regime = adx_pct.apply(
        lambda p: "insufficient_history" if pd.isna(p) else _tercile_label(p, _TREND_LABELS)
    )
    vol_regime = natr_pct.apply(
        lambda p: "insufficient_history" if pd.isna(p) else _tercile_label(p, _VOL_LABELS)
    )
```

`rolling(252)` produces `NaN` for any bar that doesn't yet have a full
252-observation window behind it — `pandas`' own `min_periods` default
requires the window to be full before it will compute anything. Rather
than let that `NaN` propagate silently into a label, every bar without a
full window gets the explicit string `"insufficient_history"` instead.
Why this reads as a *label*, not a filtered-out row the way Component 4
handled its own leading `NaN`s, is section 3's fifth decision.

### `src/mcp_tools/server.py`'s `classify_regime` tool

```python
@mcp.tool()
def classify_regime(ticker: str, start: date | None = None, end: date | None = None) -> list[RegimeRecordOut]:
    """Label each bar's trend strength and volatility level, relative to its own trailing 252-bar history."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=None, end=end)
```

The single most consequential line in this component: `start=None`, always
— the caller's actual `start` argument is never passed to `load_price_data`
here, no matter what it is. Every other tool in this stage
(`get_price_data`, `run_backtest`, `compute_indicator`) forwards the
caller's `start` straight through. This one deliberately doesn't, and the
full reasoning for why is section 3's sixth, most important decision.

```python
    regimes = _classify_regime(df)
    if start is not None:
        regimes = regimes[regimes.index >= pd.Timestamp(start)]
```

`classify_regime` (the pure function, aliased `_classify_regime` on
import — the same shadowing-avoidance pattern Component 3 already
established for `run_backtest`) runs over the *entire* loaded DataFrame.
Only after it's produced real labels for every bar does this line trim the
result down to what the caller actually asked for. `pd.Timestamp(start)`
converts the plain `datetime.date` the MCP layer already coerced the
argument into (the same coercion mechanism Component 2 confirmed) into
something directly comparable against `regimes`' `DatetimeIndex`.

```python
    return [
        RegimeRecordOut(
            date=row.Index.date(),
            adx_percentile=None if pd.isna(row.adx_percentile) else float(row.adx_percentile),
            trend_regime=row.trend_regime,
            natr_percentile=None if pd.isna(row.natr_percentile) else float(row.natr_percentile),
            vol_regime=row.vol_regime,
        )
        for row in regimes.itertuples()
    ]
```

`itertuples()`, not `iterrows()` — the same choice Component 2 made, and
for the same underlying reason: this row mixes float (`adx_percentile`,
`natr_percentile`, possibly `NaN`) and string (`trend_regime`,
`vol_regime`) columns, exactly the kind of mixed-dtype row `iterrows()`
would coerce into a single common-dtype `Series`, risking silently wrong
types the way Component 2's `Volume` column was at risk. The explicit
`None if pd.isna(...) else float(...)` conversion is necessary here in a
way it wasn't for Component 4's `compute_indicator` — that tool filtered
`NaN` rows out entirely before they ever reached a response model; this
tool deliberately keeps every row, `NaN` or not, so each percentile field
needs its own explicit `NaN`-to-`None` conversion rather than relying on a
filter to remove the problem earlier.

### `src/mcp_tools/schemas.py`'s `RegimeRecordOut`

```python
class RegimeRecordOut(BaseModel):
    date: date
    adx_percentile: float | None
    trend_regime: Literal["choppy", "neutral", "trending", "insufficient_history"]
    natr_percentile: float | None
    vol_regime: Literal["low_vol", "neutral", "high_vol", "insufficient_history"]
```

The `Literal` types on both label fields are a real, load-bearing choice,
not decoration — they mean Pydantic itself will reject any value other
than the four allowed strings per field, so if `regime.py`'s labeling logic
ever produced something unexpected (a typo in a label string, say), this
schema would catch it at the response-shaping boundary rather than letting
a malformed label reach a caller silently.

---

## 3. Design decisions and rejected alternatives

### Refactoring `indicator_compute.py` this time, after declining to refactor `_FIELD_TO_COLUMN` last time

Component 4 deliberately chose *not* to deduplicate `_FIELD_TO_COLUMN`
against `rule_strategy.py`'s nearly-identical `_FIELD_TO_ATTR`, reasoning
that a fixed, five-entry mapping onto an external library's naming
convention carries essentially no drift risk, so duplicating it was lower
risk than touching already-tested Stage 3 code for a purely cosmetic
benefit. This component makes the opposite call on a different piece of
code, and the distinction is the point: `compute_indicator`'s computation
logic (validate, resolve `spec`, build `price_args`, call `spec.fn`,
handle `None`, select the output column) is several lines of real
algorithmic work, not a static lookup table — and without extracting it,
this component would have needed *three* copies of that logic to exist
(the original inside `compute_indicator`, plus one each for `ADX` and
`NATR` inside `classify_regime`), not two. Three copies of real logic is
qualitatively different from two copies of a stable constant: any future
fix to that logic (a new edge case in `select_output_column`, say) would
need to be found and applied in three places instead of one, with no
compiler or test to catch a missed copy. The rejected alternative —
duplicating the computation twice more inside `regime.py`, matching
Component 4's stated precedent literally rather than judging each case on
its own risk — was explicitly not chosen, and the difference between the
two components' decisions was discussed and confirmed with the user before
either was written, precisely so the apparent inconsistency (duplicate
here, refactor there) reads as a considered judgment call rather than an
accident.

**Reversibility:** low-risk regardless — `compute_indicator_series` is a
pure function with no side effects, easy to inline back into both call
sites if a future reason ever emerged to do so.

### `NATR` over raw `ATR` for the volatility signal

Both are already in the core registry, already hand-verified in Stage 3 —
this wasn't a case of building new indicator math, just choosing between
two existing options. Raw `ATR` is denominated in the same units as price,
which means it scales with a stock's price level: if a ticker's price
drifts substantially within one 252-day window (a stock going from $50 to
$200, say), `ATR` trends upward with that price drift even if the stock's
*percentage* volatility hasn't actually changed. That would contaminate a
signal meant to answer "was this a high- or low-volatility period" with an
unrelated fact, "is the price higher now." `NATR` — `ATR` expressed as a
percentage of the closing price — removes that confound by construction.
The rejected alternative, plain `ATR`, was ruled out specifically because
this project already has a name for the failure mode a price-level-
contaminated volatility signal would produce: it's the same shape of
problem `.claude/rules/data-pipeline.md`'s "thresholds are relative, never
hand-picked" rule exists to prevent, just showing up as a unit-of-measure
issue instead of a hand-picked-threshold issue.

**Reversibility:** trivial — a one-line change to `_VOL_PARAMS`'s
containing call if `ATR` were ever preferred instead, with no other logic
depending on which one is used.

### Trailing, not expanding or full-sample, rolling window — verified, not assumed

`pandas.Series.rolling(window)`'s default (`center=False`) only ever looks
backward from the current row — this was confirmed directly with a small
synthetic series before `regime.py` was written, not assumed from general
`pandas` knowledge. This matters for exactly the reason the approved Stage
4 plan already named for this component: an expanding or full-sample
window would let an early bar's classification depend on `ADX`/`NATR`
values computed from years *later* than that bar — the same lookahead
shape `docs/architecture.md` §5 already flags for point-in-time universe
selection ("screening on today's data, backtesting from 2015"), one layer
down at the individual-bar level instead of the ticker-universe level. The
rejected alternative — an expanding window, which avoids ever having to
pick a fixed lookback length — was set aside for the reason the plan
itself already gives: a self-calibrating relative threshold is the goal,
but an expanding window's calibration point keeps moving further from
"recent" the longer a ticker's history gets, eventually diluting a genuine
multi-year regime shift under an ever-growing pile of older data the same
way a full-sample quantile would.

### Tercile split with an explicit "neutral" band, over a binary median split

This was the one genuinely open design fork the approved Stage 4 plan
didn't pin down in advance — unlike the 252-bar lookback window, which the
user explicitly required be settled in the plan itself before any code was
written, the label-split boundary was left for this component's own design
discussion. A binary median split (above/below the 50th percentile) was
the simpler alternative, and was rejected in favor of a three-way tercile
split with an explicit `neutral` middle band, chosen specifically because
a binary split forces a bar sitting at the 51st percentile into the same
category as one at the 99th — collapsing a real, meaningful difference in
degree into an arbitrary-feeling binary label right at the boundary. The
tercile split's middle band gives genuinely ambiguous days their own
honest label instead. This choice is consistent with a pattern already
present elsewhere in this project — disclosing uncertainty explicitly
rather than forcing a confident-looking answer a case doesn't actually
support, the same instinct behind Stage 3's `KNOWN_DEVIATIONS` mechanism
for its own gate and, within this very component, the
`"insufficient_history"` label discussed next. The raw percentile
(`adx_percentile`/`natr_percentile`) is reported alongside the categorical
label regardless, specifically so a simpler binary read remains available
to any future consumer — Stage 5's agent, most plausibly — without needing
this component's own logic to be changed or re-run to get one.

**Reversibility:** fully reversible without touching stored data — nothing
persists the categorical label independent of the percentile it was
derived from; a different split boundary is a pure function of numbers
already being computed and reported today.

### `"insufficient_history"` as an explicit label value, not a silently omitted row

Component 4's `compute_indicator` tool filtered `NaN` values out of its
response entirely — a bar with no computable `SMA` value yet simply didn't
appear in the returned list. This component makes the opposite choice for
a bar with no defined regime label yet: it appears in the output, with
both label fields set to the literal string `"insufficient_history"`
rather than the row being dropped. The distinction is deliberate, not an
inconsistency: a missing `SMA` value carries no information beyond "not
computable yet," so silently omitting it loses nothing a consumer would
have wanted to know. A missing *regime* classification is different —
"this period doesn't have enough trailing history to say anything about
its regime" is itself a fact a future consumer (most plausibly Stage 5's
agent, investigating why a strategy behaved unexpectedly during an early
period of a ticker's history) could specifically want to see stated
explicitly, rather than having to notice a shorter-than-expected row count
and infer the reason on its own. The rejected alternative — bare `None`
percentiles with no corresponding label distinction — was set aside
because a `None` value, on its own, doesn't self-document *why* it's
`None` the way a dedicated string value does; the `Literal` type on the
label fields makes `"insufficient_history"` a first-class, self-explaining
outcome rather than an absence a caller has to interpret.

### Loading a ticker's full history, always, and filtering the output afterward

This is the component's single most consequential decision, and the one
most likely to have gone quietly wrong without deliberate attention. The
naive approach — matching every other tool this stage, forwarding the
caller's `start` argument straight into `load_price_data` — has a real
correctness trap specific to this tool: `classify_regime` needs roughly
252 bars of history *before* the first bar it's asked to label, just to
give the rolling window something to rank against. A caller asking for
regime data starting in 2024, on a ticker ingested since 2010, would, under
the naive approach, only ever see the small slice of 2024 data the query
actually returned — nowhere near 252 prior bars — and every single row
would incorrectly read `"insufficient_history"`, regardless of how much
real history the ticker actually has.

Two alternatives were weighed, both flagged and discussed with the user
before either was implemented. The first — padding the database query with
an estimated calendar-day buffer (roughly 400 calendar days to comfortably
cover 252 trading days plus weekends and holidays) — was rejected as an
*approximate* fix: a calendar-day estimate carries real edge-case risk
(an unusually long market closure, a data gap) that could, in principle,
still leave the padded window short of 252 actual trading rows in some
case never tested. The chosen approach instead loads the ticker's entire
available history unconditionally (`start=None` regardless of the caller's
actual request), runs `classify_regime` over all of it, and only *then*
filters the resulting DataFrame down to the caller's requested window. This
is a *structural* guarantee rather than an estimated one: as long as the
ticker has any prior data at all, the rolling computation sees every bar
of it, with no calendar-day arithmetic that could be subtly wrong in an
untested case. The cost — recomputing `ADX`/`NATR` over a ticker's entire
history on every call, rather than a padded slice — was judged genuinely
negligible at this project's current data scale (a couple of decades of
daily bars across roughly twenty tickers), consistent with this project's
broader posture of deferring performance concerns until they're real
rather than speculative (the same reasoning behind Stage 4's own build
order putting agent-facing performance concerns after Stage 5, not before).

**Reversibility:** if this ever becomes a real performance problem at a
larger data scale, the calendar-day-padding alternative remains available
as a targeted optimization — but only once there's a real, measured cost
to justify trading structural correctness for it, not before.

---

## 4. Concepts introduced

**Lookahead bias at the labeling layer, not just the trading layer.**
Sacred Gate 1 (Stage 2) exists to prove the *backtester* never lets a
simulated trade see future data. This component is the first place in the
project where the identical concern shows up in a tool that doesn't place
any trades at all — a regime label is just as capable of leaking future
information into a supposedly historical description of a bar as a trading
signal is into a supposedly historical trade decision, if its rolling
window isn't built correctly. The trailing-window verification in section
3 is this project's first real exercise of that same discipline outside
the backtester itself.

**A rolling window's own warm-up compounding with an indicator's warm-up.**
`REGIME_LOOKBACK_BARS = 252` describes how many *valid observations* the
rolling window needs, not how many *raw price bars* must pass before the
first label appears. `ADX(14)` itself needs roughly 13 bars of its own
before producing its first non-`NaN` value (a property of how `ADX` is
computed — a smoothed measure built from several intermediate smoothed
quantities, each with its own short warm-up), so the rolling window's own
252-observation requirement only starts counting once `ADX`'s own warm-up
has passed. The two warm-ups compound rather than overlap, which is why
this component's live verification found the real transition at bar 264,
not bar 252 — a concrete instance of a general principle worth naming:
"N periods of lookback" almost always means N periods of *usable output*
from whatever's feeding the window, not N raw input rows, and the gap
between those two numbers is exactly the kind of thing worth checking
directly rather than assuming away.

---

## 5. How this component was tested

Continuing the same pattern every component this stage has followed: real
calls through `_handle_call_tool`, against the real `strategy_research`
database, both before writing (isolated synthetic checks of specific
mechanisms) and after (full end-to-end calls against real AAPL data).

**Before writing `regime.py`**: a small synthetic `pandas.Series` confirmed
`rolling(window).rank(pct=True)`'s two load-bearing properties directly —
that it requires a full window before producing a non-`NaN` value, and
that its window is trailing, never including a future position relative to
the row being ranked.

**After writing the full component, against real data:**

`compute_indicator` (Component 4's tool) was re-run with its exact original
Component 4 verification call (`SMA`, length 10, AAPL, Jan–Feb 2024) to
confirm the `indicator_compute.py` refactor changed nothing about its
externally-visible behavior — same 23 rows, unchanged.

`classify_regime(ticker="AAPL", start="2024-01-01", end="2024-01-10")` — the
direct test of this component's most consequential decision. AAPL has been
ingested since roughly 2010–2015, over a decade of history relative to the
requested 2024 start. The returned rows showed real labels immediately —
the first row: `trend_regime: "choppy"`, `adx_percentile: 25.4`,
`vol_regime: "neutral"`, `natr_percentile: 44.8` — not
`"insufficient_history"`. This is the concrete evidence the "load
everything, filter after" design actually does what it was built to do:
the naive alternative (forwarding `start` straight into `load_price_data`,
matching every other tool this stage) would have produced
`"insufficient_history"` on literally every row of this exact test.

`classify_regime(ticker="AAPL", end="2015-06-01")`, no `start` given —
loaded AAPL's complete available history and returned 1,361 total rows.
The exact `"insufficient_history"`-to-real-label transition was located
directly: the last `"insufficient_history"` row at index 263 (dated
2011-01-19), the first real-labeled row at index 264 (dated 2011-01-20,
`trend_regime: "trending"`, `adx_percentile: 75.0`) — a clean, adjacent
transition, confirming there's no intermediate, partially-defined state
between the two. This same test is what surfaced the 264-vs-252 finding
discussed in section 4.

`classify_regime(ticker="NOTAREALTICKER")` — `is_error=True`, content
`"Error executing tool classify_regime: No price data found for
NOTAREALTICKER (None – None)"`, the same shape every other tool's
unknown-ticker error has taken this stage, confirming this tool's error
path is consistent with the rest of Stage 4 rather than a special case.

Full existing 170-test suite run three times across this component — once
as a baseline before any code was written, once immediately after the
`indicator_compute.py` refactor alone (isolating whether the refactor by
itself broke anything, before `regime.py` even existed), and once after
the complete component — unchanged all three times.

**What this does not prove.** No automated, committed test exists yet for
`classify_regime`, consistent with every component this stage — Component
8 is still where formal coverage lands. This component's verification also
never exercised a ticker with a genuinely short history (fewer than ~264
bars total, where every single row would read
`"insufficient_history"`) — a real, plausible case for a newly-ingested
ticker that this component's own live testing happened not to hit. Nor did
it test what happens if `end` is set earlier than the ticker's own first
available bar, or a `start`/`end` combination that selects zero rows after
filtering.

---

## 6. Interview defense

**Q: Why does `classify_regime` (the pure function) take a `price_data`
DataFrame directly instead of a ticker, the way `compute_indicator` does?**

A: Because the MCP tool wrapping it needs to load *more* data than the
caller actually requested — the full ticker history, not just the
requested window — specifically so the rolling computation has enough
prior bars to work with. If the pure function took a ticker and loaded its
own data internally, that "load everything, filter the output after"
logic would either have to live inside the pure function (mixing session
management into what should be a testable, database-agnostic computation,
the same reasoning Components 2–4 already established for keeping pure
logic separate from session ownership) or the pure function would need its
own `start`/`end` parameters that don't actually mean what a caller would
expect them to mean. Taking a plain DataFrame keeps the pure function
simple and honest: it labels whatever data it's given, nothing more,
nothing hidden.

**Q: Why not just require callers to specify their own lookback buffer
instead of the tool silently loading everything?**

A: Because that would push a correctness requirement — "you must request
at least 252-ish bars of buffer before the date you actually care about,
or every result will silently read `insufficient_history`" — onto every
future caller of this tool, almost certainly including Stage 5's agent,
which would have no principled way to know that number without reading
this component's own implementation. A tool whose correct use depends on
a caller independently knowing an internal implementation detail is a
worse design than one that's simply correct regardless of how it's called.
The (small, currently negligible) cost of loading full history every time
buys unconditional correctness instead.

**Q (hard): This component's live verification never tested a ticker with
fewer than ~264 bars of total history — the exact case where the tool's
entire output would read `"insufficient_history"`. Given that a
newly-ingested ticker is a completely realistic scenario for this project,
isn't that a real gap in what was actually verified, not just a
theoretical edge case?**

A: It's a real, disclosed gap, not a theoretical one — worth stating
plainly rather than implying broader coverage than what actually happened.
What gives some confidence the behavior is still correct, short of having
tested it directly: the `NaN`-to-`"insufficient_history"` conversion in
`regime.py` doesn't distinguish "this ticker has a little history" from
"this ticker has none" — both produce the identical `rolling()`-driven
`NaN`, handled by the identical `pd.isna(p)` check, so there's no separate
code path that a short-history ticker would exercise differently than the
long-history ticker this component actually tested against. That's a
reason to expect it works, not proof that it does — and the honest
position, consistent with this project's own stated discipline, is that
this specific case belongs on Component 8's list of cases to actually run,
not something to claim confidently without having run it.

**Honest weaknesses, stated plainly:** no committed automated test exists
yet for this component, matching every prior component this stage. A
short-history ticker was never actually tested, only reasoned about, as
covered above. And this component's tercile-split boundaries
(`_tercile_label`'s `100/3`, `200/3`) are, like the lookback window before
them, a considered but not empirically re-derived choice — nothing in this
component's own verification checked whether a different split (quartiles,
say) would produce meaningfully different labels on real data, the way
Stage 1's screener design explicitly plans to sensitivity-test its own
relative thresholds at quintile, tercile, and decile cuts. This component
committed to one cut and reported the underlying percentile alongside it
specifically so that gap is recoverable later without re-running anything,
but it was never itself closed here.

---

## 7. What comes next and why

Component 6 (statistics tool) is next, and it's the component this stage's
approved plan calls out as needing the most upfront design work — the
significance-test choice (a Monte Carlo permutation test against a
randomized-entry control) and the multiple-comparisons correction were
both locked into the plan explicitly rather than left to be decided
mid-component, unlike this component's tercile-split question, which was
deliberately left open for exactly this kind of in-context discussion.
Component 6 will also be the first place `compute_indicator_series` (this
component's own extracted, DataFrame-based function) gets a second real
caller beyond `regime.py` itself, if the statistics tool ends up needing a
raw indicator series on data it already has in hand rather than a ticker
to look up — a concrete test of whether that refactor's anticipated future
reuse was actually justified or just a plausible-sounding guess.

If this component's central decision — loading full history and filtering
after, rather than trusting a caller-supplied window — turns out wrong in
some case not yet tested (the short-history-ticker gap named above being
the most concrete candidate), the most likely symptom is not a crash: it's
every row of a short-history ticker's regime data reading
`"insufficient_history"`, which is either the honestly correct answer (the
ticker genuinely doesn't have enough history yet) or a bug silently
producing the same-looking output as the correct case — a real, if
narrow, ambiguity this component's own verification didn't get to resolve.
