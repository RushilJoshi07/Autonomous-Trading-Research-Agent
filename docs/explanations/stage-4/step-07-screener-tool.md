# Step 7 — Screener Tool (Stage 4)

## 1. What this does

`screen_universe(sector=None, industry=None, metric="liquidity", lookback_days=63,
as_of=None) -> ScreenerResult` is the sixth and final planned MCP tool this
stage, and the one architecture.md §5 describes as resolving "liquid tech
names" (or any sector/metric combination) into an actual, rankable ticker
list — the piece `docs/architecture.md`'s own charter-confirmation step
depends on. It filters the ingested universe by sector/industry (a lookup
against `TickerMetadata`) and ranks the result by a computed, relative
metric — liquidity or volatility — expressed as a percentile within the
matched group, using only price data available as of a caller-specified
reference date.

Getting this tool to be genuinely useful required a real data action first:
broadening the ingested universe from what turned out to be, in practice,
a single ticker. Section 3 covers that in full, including a real,
previously-undiscovered gap this component surfaced before any of its own
code was written.

What this component is *not*: it does not decide which sector or metric
matters for any particular research question — that's an agent's job,
starting Stage 5. It does not backfill point-in-time sector history (a
real, disclosed limitation covered below). It ranks whatever it's asked to
rank, honestly, including telling the caller when the group being ranked
is too small for the ranking to mean much.

---

## 2. Every meaningful line explained

### The universe, before any screener code existed

Before writing anything, `universe.py`'s usage was checked directly — not
assumed to be a throwaway Stage 1 artifact. `data_pipeline/cli.py` imports
`TICKERS` from it and uses it for `python -m data_pipeline.cli
ingest/refetch/check-actions`, the project's actual standing
nightly-refresh entry point (architecture.md §6's documented refresh
rhythm). Retiring the static list meant this operational tool needed a
replacement, not just a deletion.

Querying the database comprehensively — something no prior component this
stage had needed to do, since every one of them was tested exclusively
against `AAPL` — surfaced a real, pre-existing gap: of the 17 tickers
`universe.py` listed, only `AAPL` had ever actually been ingested. The
other 16 (`AMZN`, `BAC`, `BROS`, `CROX`, `CVX`, `GOOGL`, `GS`, `JNJ`, `JPM`,
`META`, `MSFT`, `PG`, `SPY`, `UNH`, `WMT`, `XOM`) had zero price rows, zero
metadata, and zero ingestion-run history — not corrupted or partially
ingested, simply never run against this database at all. This wasn't a bug
this component introduced; it was a fact about the database's actual state
that had gone unnoticed because nothing before this component had reason
to look at anything beyond `AAPL`. It was surfaced to the user immediately,
rather than silently building the screener against a smaller-than-discussed
universe or quietly working around the gap.

### Broadening the universe

37 new tickers were ingested through the exact path the approved Stage 4
plan's Decision 2 already committed to — `ingest_daily(new_tickers)` then
`check_corporate_actions(new_tickers)`, the same retry-wrapped,
corporate-actions-aware pipeline Stage 1 built and tested, nothing
bespoke. Zero failures. With the user's explicit go-ahead once the
17-ticker gap was disclosed, the 16 missing original tickers were then
ingested through the identical path — also zero failures. The database now
genuinely holds 54 tickers with both metadata and price data, matching
what had actually been discussed and approved, not a smaller number
quietly substituted for it. The real, resulting sector distribution (from
real `yfinance` metadata, not hand-assigned) came back sensible: Technology
9, Financial Services 8, Communication Services 7, Healthcare 7, Energy 6,
Consumer Cyclical 6, Consumer Defensive 5, Industrials 5, and one `None`
— `SPY`, the ETF, correctly reporting no sector at all, which is exactly
what `universe.py`'s own original comment said it was included to test.

### `universe.py`, rewritten

```python
def all_tickers(session: Session) -> list[str]:
    """Every ticker with metadata currently ingested, sorted."""
    rows = session.execute(select(TickerMetadata.ticker)).scalars().all()
    return sorted(rows)
```

One function replaces the static list. `cli.py`'s three commands now call
this instead of importing `TICKERS` directly. The practical effect: the
standing ingestion commands automatically cover whatever's actually in
`TickerMetadata` from now on — today's 37 new tickers, the 16 backfilled
ones, and anything added later — with no future hand-edit to a Python list
ever required again. One boundary worth stating explicitly rather than
leaving implicit: this function can only ever refresh *already-known*
tickers. A ticker with no `TickerMetadata` row yet cannot appear in a
query *of* `TickerMetadata`, so onboarding a genuinely new ticker for the
first time still has to be a deliberate, explicit action — precisely what
this component's own one-time broadening already did directly, not
something `all_tickers()` itself is meant to handle.

### `data_pipeline/screener.py`

```python
_MIN_OBSERVATIONS = 5
```

A floor below which a ticker is excluded from ranking entirely, rather than
assigned a metric computed on too little data — why this specific number
and this specific handling (exclusion, not a null placeholder) is section
3's sixth decision.

```python
def _metric_value(session, ticker, metric, lookback_days, as_of) -> float | None:
    stmt = select(PriceBar.adj_close, PriceBar.adj_volume).where(PriceBar.ticker == ticker)
    if as_of is not None:
        stmt = stmt.where(PriceBar.date <= as_of)
    stmt = stmt.order_by(PriceBar.date.desc()).limit(lookback_days)
```

The `date <= as_of` filter is the single line responsible for this
component's central correctness property — without it, every computed
metric would always reflect the *latest* available data regardless of what
date a caller claims to be screening from. `.order_by(...desc()).limit(lookback_days)`
takes exactly `lookback_days` rows counting backward from `as_of` (or from
the most recent data, if `as_of` is `None`) — a row-count-based window, not
a calendar-day estimate. Why that specific choice, not a padded date range,
is section 3's first decision.

```python
    rows = session.execute(stmt).all()
    if len(rows) < _MIN_OBSERVATIONS:
        return None

    close = pd.Series([float(r.adj_close) for r in reversed(rows)])
```

`reversed(rows)` restores chronological order — the query itself fetched
rows newest-first (to make `LIMIT` select the most recent `lookback_days`
rows), but `pct_change()` (used below for volatility) needs its input in
actual time order to compute day-over-day returns correctly; computing it
on a newest-first series would silently produce returns comparing each day
to the *following* day instead of the *preceding* one.

```python
    if metric == "liquidity":
        volume = pd.Series([float(r.adj_volume) for r in reversed(rows)])
        return float((close * volume).mean())
    if metric == "volatility":
        returns = close.pct_change().dropna()
        if len(returns) < 2:
            return None
        return float(returns.std())
    raise ValueError(f"unknown metric {metric!r}")
```

Liquidity is mean dollar volume (`close × volume`, averaged over the
window) — the standard practitioner definition, and directly comparable
across tickers regardless of share price, unlike raw share volume alone.
Volatility is the standard deviation of daily percentage returns — the
second `len(returns) < 2` check exists because `_MIN_OBSERVATIONS = 5`
guarantees enough *price* rows, but `pct_change().dropna()` always produces
one fewer *return* than there were prices, and a standard deviation
computed on 0 or 1 values is undefined; this is the same defensive
instinct as `research_stats/confidence.py`'s own `n < 2` check, applied to
a different computation.

```python
def screen(session, sector=None, industry=None, metric="liquidity", lookback_days=63, as_of=None) -> ScreenerResult:
    stmt = select(TickerMetadata)
    if sector is not None:
        stmt = stmt.where(TickerMetadata.sector == sector)
    if industry is not None:
        stmt = stmt.where(TickerMetadata.industry == industry)
    metas = {m.ticker: m for m in session.execute(stmt).scalars().all()}
```

The metadata filter narrows the candidate group *before* any per-ticker
metric computation runs — a ticker outside the requested sector never has
`_metric_value` called for it at all, which matters both for correctness
(only tickers actually matching the filter should ever be ranked) and for
avoiding wasted computation on tickers that were never going to be included
anyway.

```python
    values = {}
    for ticker in metas:
        value = _metric_value(session, ticker, metric, lookback_days, as_of)
        if value is not None:
            values[ticker] = value

    if not values:
        return ScreenerResult(group_size=0, candidates=[])
```

A ticker whose `_metric_value` returned `None` (too little data) is
silently absent from `values` — not present with a placeholder. An empty
`values` dict (every matched ticker excluded, or no tickers matched the
filters at all) returns a valid, empty `ScreenerResult` rather than raising
— an empty result is a legitimate, structurally-representable outcome, not
an error condition.

```python
    ranked = sorted(values, key=lambda t: values[t], reverse=True)
    n = len(ranked)

    candidates = []
    for rank, ticker in enumerate(ranked):
        percentile = 100 * (n - 1 - rank) / (n - 1) if n > 1 else 100.0
```

Rank 0 (the highest metric value) gets percentile 100; the lowest gets
percentile 0; everything else is evenly spaced between them. The `n > 1`
guard exists because the formula divides by `n - 1`, which is zero for a
single-candidate group — that lone candidate is reported at percentile
100 by convention (it's simultaneously the highest and lowest value in a
group of one), rather than the code raising a `ZeroDivisionError` on a
legitimate, if degenerate, input.

### `screen_universe`, the MCP tool

```python
@mcp.tool()
def screen_universe(
    sector: str | None = None,
    industry: str | None = None,
    metric: Literal["liquidity", "volatility"] = "liquidity",
    lookback_days: int = 63,
    as_of: date | None = None,
) -> ScreenerResult:
    """Rank tickers by relative liquidity or volatility percentile within a sector/industry group, computed only from data as of a given date (point-in-time — no lookahead into universe selection)."""
    with SessionFactory() as session:
        return _screen(session, sector=sector, industry=industry, metric=metric, lookback_days=lookback_days, as_of=as_of)
```

The thinnest wrapper of any tool this stage — `screen` (aliased `_screen`
on import, the same shadowing-avoidance pattern every tool this stage has
used) already returns a fully-formed `ScreenerResult`, so the tool body is
purely session lifecycle management, nothing else to compose. No new
`mcp_tools/schemas.py` entries were needed — `ScreenerResult` and
`ScreenerCandidate` are real Pydantic models already defined in
`data_pipeline/screener.py`, returned directly, the same precedent
`BacktestResult` (Component 3) and every `research_stats` model (Component
6) already established.

---

## 3. Design decisions and rejected alternatives

### Point-in-time correctness via `as_of` — the central design question, one layer above `regime.py`'s own fix

Architecture.md §5 names this exact failure mode explicitly, for this exact
tool: "screening on today's data, backtesting from 2015 uses future
information to select the universe." The rejected alternative — always
using the latest available data, with no `as_of` parameter at all — was
never seriously considered, precisely because the architecture document
already identifies it as a real lookahead-bias mechanism, not a
theoretical concern. The lookback window itself uses a row-count `LIMIT`
(order by date descending, take `lookback_days` rows, reverse to
chronological order) rather than a calendar-day-padded date range. This is
not a new decision invented for this component — it's the same choice
Component 5's `classify_regime` already made and the user already
confirmed as correct there, for the identical underlying reason: a
calendar-day estimate carries real edge-case risk (holidays, unusual
closures) that a row-count `LIMIT` simply doesn't have, since it counts
actual ingested trading rows rather than estimating how many calendar days
should contain them.

**Reversibility:** load-bearing, not a cosmetic choice — removing the
`as_of` filter would silently reintroduce exactly the lookahead bias
architecture.md warns about, for every future study built on this tool.

### Percentile alone, not six boolean quintile/tercile/decile flags

The original design sketch (discussed with the user before any code was
written) included `in_top_quintile`, `in_bottom_decile`, and four similar
boolean fields alongside `percentile`. These were dropped before writing
`screener.py` at all. The reasoning: because `screen_universe` already
returns *every* ticker in the matched group, not just the ones passing some
pre-selected cut, "does this ticker survive a quintile/tercile/decile cut"
is already fully recoverable by a caller directly from the single
`percentile` number (`percentile <= 20` is the bottom quintile,
`percentile >= 90` is the top decile, and so on) — six additional boolean
fields would have hard-coded specific cut values into the schema without
adding any capability a caller couldn't already derive themselves from a
number already being returned. The approved plan's "sensitivity testing is
part of the tool's output, not a separate manual step" requirement is
satisfied by the *transparency of the full ranked group*, not by
pre-computing redundant flags on top of it.

**Reversibility:** trivial to add back later if a real caller (Stage 5's
agent, most plausibly) turns out to want pre-computed flags rather than
computing the comparison itself — nothing about today's design forecloses
that.

### `group_size` reported once, at the top level, not per-candidate

A decile cut computed from a four-ticker sector is close to meaningless —
at most one ticker could ever occupy "the top 10%" of a group that small.
The rejected alternative — repeating `group_size` as a field on every
`ScreenerCandidate` — was set aside as pure redundancy (the value is
identical across every candidate in one response) in favor of a single,
prominent field on the wrapping `ScreenerResult`. This mirrors a pattern
already established twice this stage: `.claude/rules/data-pipeline.md`'s
survivorship-bias coverage disclosure, and Component 6's
`null_mean_sharpe`/`null_std_sharpe` fields reporting the shape of a
distribution alongside a single derived number. Disclosing the size of the
group a percentile was computed against is the same instinct as both —
don't let a thin sample present itself with the same apparent authority as
a well-populated one.

### Sector/industry metadata is explicitly *not* point-in-time — disclosed, not hidden

`as_of` makes the *computed* metrics (liquidity, volatility) genuinely
point-in-time correct. It does nothing for sector or industry, because
`TickerMetadata` has no history at all — one row per ticker, overwritten on
each re-ingestion, with no record of what a ticker's sector *was* at some
past date. A sector filter therefore always reflects whatever the most
recent ingestion happened to record, regardless of what `as_of` a caller
passes. The rejected alternative was building point-in-time sector
tracking (a history table, effective-dated rows) as part of this
component. That was set aside for the same reason architecture.md §6
itself gives for not fully solving point-in-time universe *membership*:
it's a real, disclosed limitation of what free data actually supports, and
building it properly is its own separate project, not something to
half-solve inside an already-large component. The honest move, matching
architecture.md's own treatment of the survivorship-bias gap, is stating
the limitation plainly rather than either hiding it or pretending to solve
it partially.

**Reversibility:** not reversible without a genuinely new data source or a
history-tracking schema change — this is a real, structural gap in what
this component (or this project's free data sources) can currently
guarantee, not a quick fix deferred for convenience.

### `_MIN_OBSERVATIONS = 5`, and exclusion rather than a null value

A ticker with fewer than 5 price rows in the requested lookback window is
dropped from the ranked group entirely — it never appears in
`ScreenerResult.candidates` at all, rather than appearing with
`metric_value: null`. This is the third time this exact "don't force a
value onto too little data" instinct has shaped a design decision this
stage: Component 5's regime classifier reports an explicit
`"insufficient_history"` label rather than a quantile computed on a
partial window, and Component 6's `bootstrap_ci` refuses outright rather
than computing a confidence interval from a single value. Exclusion,
specifically, was chosen over a null placeholder here (rather than
Component 5's explicit-label approach) because a screener candidate that
can't be meaningfully ranked isn't really a *candidate* for this query at
all — there's no equivalent to Component 5's "this bar exists and has a
date, it just doesn't have a regime yet" framing; a ticker excluded here
simply doesn't belong in this particular ranked comparison.

---

## 4. Concepts introduced

**Point-in-time universe selection, made concrete rather than abstract.**
Architecture.md names this concern in prose; this component is the first
place in the codebase it's actually implemented and testable. The COVID-
crash-versus-calm-period demonstration in section 5 is what makes the
concept legible as a real risk rather than an abstract warning: the same
tickers, ranked by the same metric, produce a genuinely different ordering
depending on what date the caller claims to be screening from — which is
exactly the mechanism by which an un-dated screener query would leak future
information into a study's universe selection.

**Why a database's actual state can silently diverge from what a
configuration file claims.** `universe.py`'s 17-ticker list looked, by
every reasonable reading of the code, like a description of what had been
ingested. It wasn't — it described what was *intended* to be ingested,
which had quietly stopped matching reality at some earlier point this
project's history doesn't preserve a record of. The lesson this component
draws out explicitly: a list of names in source code is a claim, not a
verified fact about a running system's actual state, and the only way to
know whether they still match is to query the system directly rather than
read the file and assume.

---

## 5. How this component was tested

Every check below ran against the real, now-54-ticker database, through
the actual protocol handler (`_handle_call_tool`) — no synthetic stand-ins,
consistent with every component this stage.

**Happy path:** `screen_universe(sector="Technology", metric="liquidity")`
returned `group_size: 9` with a fully ranked list — `NVDA` most liquid
(~$30B average daily dollar volume), `ADBE` least (~$1.5B) — real numbers
consistent with these specific stocks' actual, well-known trading
characteristics, not just plausible-looking output.

**Empty group:** `screen_universe(sector="NotARealSector")` returned
`{"group_size": 0, "candidates": []}` — a valid, structured empty result,
not an exception, matching the approved Stage 4 plan's own Component 9
checklist item written specifically for this tool.

**The point-in-time proof, specifically requested before this component
was built** ("show me a concrete example proving `as_of` actually changes
the returned results between a past date and today — not just that the
parameter exists"). `screen_universe(sector="Technology",
metric="volatility")` was run twice: once with `as_of="2020-03-23"` (a
63-day trailing window landing squarely in the COVID crash) and once with
`as_of="2024-06-01"` (a calm, more recent period). Every ticker's daily
volatility came back roughly double in the crash window — `AAPL`: 0.0387
(crash) versus 0.0154 (calm) — real, expected market behavior, not an
artifact of the test. More importantly than the absolute magnitude change:
the *relative ranking* genuinely shifted, not just uniformly rescaled.
`INTC` ranked highest-volatility (percentile 100) during the crash but
fell to third (percentile 75) in the calm period. `AAPL` and `MSFT`
literally swapped relative order — `AAPL` sat below `MSFT` during the
crash (percentiles 12.5 versus 25) and above it in the calm period
(25 versus 12.5). Same tickers, same code, only the `as_of` argument
changed, and the actual group composition a caller would see genuinely
differs — direct evidence the point-in-time design does real, load-bearing
work rather than being plumbing nobody's result actually depends on.

**Error path:** an invalid `metric` value (`"not_a_real_metric"` instead of
`Literal["liquidity", "volatility"]`) was rejected at the SDK's own
argument-coercion layer, before the tool body ran at all —
`is_error=True`, content `"1 validation error for
screen_universeArguments\nmetric\n  Input should be 'liquidity' or
'volatility'"`. This confirms the same mechanism Component 3's
`StrategyRule` discriminated-union validation already established (SDK-level
rejection of a malformed argument before any tool logic executes) also
applies cleanly to a plain `Literal` type, not just a nested Pydantic
model.

Full existing 170-test suite run and confirmed unchanged after every code
change this component made, including the `cli.py` update.

**What this does not prove.** No automated, committed test exists yet for
`screen_universe` or `screener.py`, consistent with every component this
stage — Component 8 is still where formal coverage lands. Nothing in this
component's verification exercised the `industry` filter (only `sector`
was tested), the `liquidity` metric under `as_of` (only `volatility` got
the point-in-time demonstration), or a group that starts non-empty but
shrinks to empty *after* the `_MIN_OBSERVATIONS` exclusion (as opposed to
a sector that matches zero tickers before any metric computation runs at
all) — a real, narrower gap than "the empty-result path was tested"
might suggest.

---

## 6. Interview defense

**Q: Why does the screener query `PriceBar` directly with raw SQLAlchemy
instead of reusing `backtester.data_loader.load_price_data`, the way every
other tool this stage does?**

A: Because it only ever needs two columns — close price and volume — and
`load_price_data` is built to produce a full OHLCV DataFrame shaped
specifically for `backtesting.py`'s expectations (capitalized column
names, a `DatetimeIndex`, all five price/volume fields). Reusing it here
would mean paying for a shape conversion this component never uses, for no
benefit — `data_pipeline` already has its own established pattern of
querying its own models directly (`ingest/upsert.py`'s
`get_last_cached_date` is the same style), and this component follows
that existing convention rather than reaching across a package boundary
for machinery built for a different consumer's needs.

**Q: The universe went from 17 (claimed) to 1 (actual) to 54 (real, now).
Doesn't that mean every study this project has run before today — the
Stage 3 gate included — was implicitly running against a "universe" of one
ticker, regardless of what any configuration said?**

A: Yes, and that's worth stating plainly. Stage 3's gate explicitly ran
against `AAPL` only (`docs/explanations/stage-3/stage-3-summary.md`
section 5 names this directly as a stated limitation, not something this
component is newly disclosing) — nothing about that gate's own claims was
ever resting on the other 16 tickers actually existing. The risk this
discovery closes isn't retroactive; it's forward-looking. Before this
component, any future call to a Stage 4 tool with a ticker other than
`AAPL` would have failed with "no price data found," silently limiting
every one of this stage's own tools to a one-ticker universe regardless of
what any tool's documentation implied. This component is what makes that
silent limitation stop being true, for every tool built so far this stage,
not just the screener.

**Q (hard): The `as_of` demonstration used `volatility`, not `liquidity` —
and used `sector` filtering, not `industry`. Given how central the
point-in-time claim is to this component's whole justification, isn't
testing only one of two metrics under only one of two metadata filters a
meaningfully incomplete verification of the actual claim being made?**

A: For the *specific mechanism* being verified — that `date <= as_of`
genuinely constrains which rows a metric computation sees — yes, one
metric under one filter is sufficient, because both metrics and both
filters share the identical `_metric_value` function and the identical
`as_of`-gated query; there's no code path where `liquidity` or `industry`
filtering could behave differently with respect to `as_of` without an
entirely separate, untested function existing to diverge in. But that's a
structural argument, not a substitute for having actually run it — and
it's honest to say plainly that `liquidity`'s specific behavior under
`as_of`, and `industry`'s specific filtering behavior at all, were never
independently confirmed against real data in this component's own
verification. That gap is exactly the kind Component 8's formal test suite
exists to close with actual assertions, not left open on the strength of a
structural argument alone.

**Honest weaknesses, stated plainly:** no automated test exists yet for
this component, matching every one before it this stage. The `industry`
filter and the `liquidity` metric's point-in-time behavior were never
independently verified, only reasoned about as covered above. And the
point-in-time gap for sector/industry metadata itself — not something this
component tried to solve, but worth restating here rather than only in
section 3 — means a screener call is never *fully* point-in-time correct,
only point-in-time correct for the computed half of what it filters on.

---

## 7. What comes next and why

All six of Stage 4's planned tools now exist: backtester, market data,
indicators, regime classifier, statistics, and now the screener. Component
8 (the formal automated test suite) is next — every tool built across
Components 2 through 7 has so far been verified only through real,
interactive calls against real data, never through a committed, repeatable
pytest suite. That gap has been disclosed honestly at the end of every
single step explainer this stage, component by component; Component 8 is
where all of those disclosed gaps get closed at once, in a single formal
pass, rather than remaining seven separate open items. Component 9 — the
actual stage gate, manually calling every tool through real MCP, happy
path and invalid input alike — follows directly after, and is the point at
which Stage 4's own literal, stated gate criterion ("call each manually
through MCP before any agent touches them") is finally satisfied in full,
not just approximated by the interactive testing this stage has relied on
so far.

If this component's central claim — that `as_of` genuinely gates what data
a metric computation can see — turned out to be subtly wrong in some case
this stage's verification didn't reach (the untested `liquidity`/`as_of`
combination named above being the most concrete candidate), the most
likely symptom would not be a crash. It would be a screener result that
looks entirely plausible while quietly reflecting data from after the
date a caller claimed to be screening from — precisely the kind of
lookahead bias this component's entire design exists to prevent, and
precisely the kind of failure that would only become visible if a future
Stage 5 study's results were independently re-derived and found not to
match, rather than being visible in the screener's own output at the time
it ran.
