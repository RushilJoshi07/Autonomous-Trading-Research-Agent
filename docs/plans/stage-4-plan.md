# Stage 4 — Tools via MCP

## Context

Stage 3 closed on commit `6ee2fec`: schema, two-tier indicator registry, evaluator,
interpreter, `BacktestResult` provenance, `llm_client`, and the gate all landed;
170/170 tests pass; the literature-consistency gate passed on real AAPL data (3
clean, 1 disclosed deviation); Alembic is stamped at a baseline on both databases.
`docs/explanations/stage-3/stage-3-summary.md` §7 names Stage 4 explicitly as next:
wrap the backtester, indicator registry, screener, regime classifier, and
statistics module as MCP servers.

Per `docs/architecture.md` §9, Stage 4's deliverable is six tools — backtester,
market data, indicators, regime classifier, statistics, screener — wrapped for
MCP access (§5 Step 4: "Each tool is an MCP server"; Component 1 below flags and
resolves the one place this plan reads that sentence as shorthand rather than a
literal process-topology requirement), with the stage gate being that **each is
callable manually through MCP before any agent touches them** (no agent exists
yet — Stage 5 is next). No LLM enters the runtime path in this stage (`CLAUDE.md`
"Build order" table, LLM column: "No").

## What already exists vs. what Stage 4 builds (confirmed by reading the code, not assumed)

| Tool | Backing logic | Status |
|---|---|---|
| **Backtester** | `backtester.engine.run_backtest`, `backtester.strategies.rule_strategy.make_rule_strategy`, `backtester.schema.StrategyRule` | **Fully built** (Stage 2/3). Stage 4 adds only an MCP wrapper. |
| **Market data** | `backtester.data_loader.load_price_data` | **Fully built** (Stage 2). Stage 4 adds only an MCP wrapper. |
| **Indicators** | `backtester.registry.ALL_INDICATORS` (150 specs, core+extended) | **Registry built**, but nothing computes one standalone outside `make_rule_strategy`'s `self.I()` wiring — needs one small new function. |
| **Regime classifier** | — | **Not built.** Core registry already has `ADX`, `ATR`, `NATR`, `TRUE_RANGE`, `DMP`/`DMN` — the classifier reuses these, doesn't invent new indicator math. |
| **Statistics** | — | **Not built.** `scipy` is a settled §7 stack choice but is not yet a dependency (`pip show scipy` confirms not installed; not in `pyproject.toml`). |
| **Screener** | `data_pipeline.universe.py` (static 17-ticker list) | **Not built** — that file's own comment says "The screener tool (Stage 4) will replace this list." `TickerMetadata` (sector/industry) already exists for the metadata-filter half; the computed-filter half (liquidity) is new. |

Also confirmed: no `mcp` package installed or referenced anywhere in the repo yet —
this stage introduces it for the first time.

---

## Component 1 — Dependencies and MCP scaffolding

- Add `mcp` (official Python SDK) and `scipy` to `pyproject.toml`. `scipy` is
  already the settled choice in `docs/architecture.md` §7 — this just turns that
  decision into an installed dependency; it isn't a new library choice.
- Built with the SDK's high-level decorator API (`@mcp.tool()` over a plain
  function with type hints), keeping each tool a thin wrapper rather than
  hand-rolled JSON-RPC.
- Transport: stdio. These run locally, launched as subprocesses by whatever calls
  them (a manual verification script now, LangGraph's MCP client in Stage 5) —
  consistent with "stay local through Stage 7" (architecture.md §8 cost
  discipline). No network server, no auth surface to build prematurely.

**Process topology — flagged deviation from architecture.md's literal wording, decided explicitly rather than silently:**

Architecture.md §5 Step 4 says "Each tool is an MCP server," which read literally
means six separate processes. Raising it rather than quietly picking a reading,
per `CLAUDE.md`'s instruction to flag anything that looks like it deviates from
a deliberate decision in that document.

**Recommendation: one MCP server process exposing all six tools**, not six
processes. Reasoning:
- MCP does not require one-tool-per-server — a single server instance
  registering six `@mcp.tool()` functions is completely idiomatic (this is how
  most real-world MCP servers work, e.g. one GitHub MCP server exposes dozens of
  tools). Nothing about the protocol ties "a tool" to "a process."
- The actual Stage 4 gate — "each callable manually through MCP before any
  agent uses it" — is about each *tool* being independently invocable and
  verified, not about process boundaries. A single server with six registered
  tools satisfies that identically: `get_price_data` is called, verified, and
  can fail independently of `run_backtest`, whether they share a process or not.
- These six tools share no isolation need. They're all deterministic, read-only
  (DB reads or pure computation), running as the same trusted local code with
  the same trust level — nothing here resembles a case where one tool's failure
  should be walled off from another's, the usual reason to pay for separate
  processes.
- Architecture.md §5 Step 4 also describes the execution loop as strictly
  sequential — `decide_next_action → execute_tool → write to state → repeat`,
  one tool call per iteration, never concurrent. Six processes would buy no
  real parallelism Stage 5 could actually exploit, since the loop never calls
  two tools at once.
- Operationally, one process is strictly less for Stage 5 to manage: one
  subprocess lifecycle, one client connection, one place DB-session setup can
  drift out of sync, instead of six. This matters more than it might look —
  the project's own cost-discipline section (§8) already warns that unnecessary
  running infrastructure is the most common source of silent waste.
- The individual tool implementations (the pure functions each wraps) are
  identical either way. Splitting one server instance into six later, if this
  reasoning turns out to be wrong, is a mechanical refactor of the thin adapter
  layer only — nothing about Components 2–7's actual logic would change.

This reads architecture.md's sentence as informal shorthand for "there are six
tools, MCP-wrapped" rather than a considered infrastructure decision — nothing
elsewhere in the document discusses process count, ports, or isolation
requirements for these tools.

- New top-level package `src/mcp_tools/` holds one server module
  (`server.py`) registering all six tools, plus the thin adapter logic for each:
  parse the MCP call's arguments, call one existing pure function, shape the
  return value. No decision-making logic lives in this package — that would
  violate "model proposes, code disposes" one layer early, before there's even
  a model in the loop yet to propose anything.
- Domain logic for genuinely new tools stays in the package that already owns
  that domain's data (`backtester/` for anything touching indicators or
  backtests, `data_pipeline/` for anything querying `PriceBar`/`TickerMetadata`),
  matching how Stage 2/3 already separated pure computation from any particular
  interface. One new top-level package is needed for statistics — **not named
  `statistics`**, since that would shadow Python's own stdlib `statistics`
  module for any other file in the project that does `import statistics`.
  Proposed name: `src/research_stats/`.

## Component 2 — Market data tool

Wraps `load_price_data(ticker, session, start, end)` directly — zero new logic.
One MCP tool: `get_price_data(ticker, start, end) -> OHLCV rows`. This is the tool
that makes "the agent reads the database, never the network" (`.claude/rules/data-pipeline.md`)
concrete at the interface layer: the MCP tool has no fetch path at all, only a
DB read, so there is structurally no way for a future agent to reach `yfinance`
through this tool even by mistake.

## Component 3 — Backtester tool

Wraps `run_backtest` + `make_rule_strategy` + `StrategyRule`. One MCP tool:
`run_backtest(rule: StrategyRule JSON, ticker, start, end, commission, cash) ->
BacktestResult`. Pydantic validation of the incoming `StrategyRule` is already
enforced by `schema.py` — the wrapper gets that for free by constructing a
`StrategyRule` from the arguments before calling `make_rule_strategy`; a malformed
rule fails at the same validation boundary it already fails at today, just
reached via MCP instead of a direct Python call.

## Component 4 — Indicators tool

New small pure function (`backtester/indicator_compute.py`):
`compute_indicator(ticker, name, params, session, start, end) -> pd.Series`,
reusing `IndicatorSpec.fn` and `select_output_column` from `indicators.py` —
the same two pieces `rule_strategy.py` already uses, just invoked standalone
instead of wired through `backtesting.py`'s `self.I()`. Two MCP tools:
`list_indicators()` (dumps `ALL_INDICATORS` metadata — name, tier, verified,
params, bounds) and `compute_indicator(...)`.

## Component 5 — Regime classifier tool

New `backtester/regime.py`: `classify_regime(price_data) -> DataFrame` labeling
each bar trending/choppy (via `ADX`) and high/low-vol (via `ATR`/`NATR`), reusing
the same registry entries the indicator tool exposes — no new indicator math.
Per `.claude/rules/data-pipeline.md`'s "thresholds are relative, never
hand-picked" rule (stated for the screener, but the same overfitting risk
applies here): trending/choppy and high/low-vol labels are drawn from each
ticker's own historical ADX/ATR quantiles, not a hand-picked absolute level
(e.g. "ADX > 25") that would silently stop meaning the same thing on a
different ticker or a different volatility era.

**Lookback window, pinned down now, not left implicit:** a **252-trading-day
(≈1 calendar year) trailing rolling window**, named as a single constant
(`REGIME_LOOKBACK_BARS = 252` in `regime.py`, the same pattern `indicators.py`
already uses for `MAX_LOOKBACK`) — not an expanding/full-sample window, and not
left as a free parameter per call.

- **Trailing, not expanding, and not full-sample — this is a lookahead
  concern, not just a stylistic choice.** The bar-t quantile is computed only
  from bars `[t-252, t]`. An expanding or full-sample window would let a bar
  early in a ticker's history be classified using ADX/ATR values from years
  later than that bar — exactly the "screening on today's data, backtesting
  from 2015" lookahead pattern architecture.md §5 already calls out for the
  screener, one layer down at the per-bar level instead of the universe level.
  If the regime classifier is ever used by the agent to help decide something
  about a specific point in time (Step 4's own example: "out-of-sample 0.21 →
  investigate why → call the regime classifier"), a lookahead-safe classifier
  is the only version that's safe to reuse for that.
- **Why 252 and not shorter or longer:** it's the annualization convention this
  codebase already uses (`backtesting.py`'s own `Return (Ann.) [%]`, already
  surfaced in `BacktestResult`) — reusing it here avoids introducing a second,
  inconsistent notion of "a year" into the same project. It's long enough to
  contain more than one sub-regime (so the quantile reflects real variation,
  not just last quarter's persistent state relabeling itself "normal"), and
  short enough that a genuine multi-year regime shift eventually shows up in
  the baseline rather than being permanently diluted — the same trade-off an
  expanding window would get wrong in the other direction, the same way a
  full-sample quantile would.
- **Bars before the 252nd for a given ticker have no defined regime label** —
  the tool returns an explicit "insufficient history" marker for those bars
  rather than a quantile computed on too little data to be meaningful. This
  mirrors the project's existing pattern of disclosing an evidence gap instead
  of forcing an answer (architecture.md §9 Step 9's "known-caveat" golden-set
  case, and the survivorship-bias coverage disclosure in §6) rather than a new
  one-off invented for this tool.

## Component 6 — Statistics tool

*(Addressed in full below.)*

## Component 7 — Screener tool

*(Ingestion path addressed in full below.)*

- New `data_pipeline/screener.py`. Metadata filters (sector/industry) are lookups
  against `TickerMetadata`, already populated by Stage 1's ingestion. Computed
  filters (liquidity = average daily dollar volume over a lookback window) are
  computed from `PriceBar`.
- Thresholds are relative (quintile/tercile/decile within the query, never a
  hand-picked absolute number), per architecture.md §5 and `.claude/rules/data-pipeline.md`.
  Sensitivity testing (does the result survive quintile *and* tercile *and*
  decile cuts) is part of the tool's output, not a separate manual step.
- `universe.py`'s static 17-ticker list is retired as the source of truth once
  the screener exists, replaced by a query over whatever tickers are actually in
  `TickerMetadata` — but the screener never fetches; it only filters what Stage 1
  has already ingested, preserving "reads the database, never the network."

## Component 8 — Formal automated test suite

Pytest coverage per tool's pure logic (indicator computation, regime labeling,
screener filters, statistics functions), following the same discipline
Stage 3 closed with: prove each test isn't vacuous by checking it fails the
right way when the logic it guards is broken, not just that it passes once
written.

## Component 9 — Manual MCP verification (the actual stage gate)

Architecture.md's literal Stage 4 gate: "call each manually through MCP before
any agent touches them." A small verification script drives the server over
stdio as a real MCP client would — list its tools, call each. This is the same
"verify by execution, don't trust a claim" discipline `stage-3-summary.md`
names as the throughline of every real bug found so far, applied to the
interface layer instead of the computation layer.

**Happy path alone is not sufficient — each tool also gets at least one
invalid-input call, and the check is that MCP surfaces a clean, structured
error, not a crash, a hang, or a silently-wrong result.** Concretely, per tool:

- **Market data:** an unknown ticker (`"NOTAREALTICKER"`) → `load_price_data`'s
  existing `ValueError("No price data found for ...")` should surface as a
  structured error, not an empty success payload.
- **Backtester:** a malformed `StrategyRule` (unknown indicator name, or a
  positive/lookahead offset) → the existing Pydantic `ValidationError` from
  `schema.py` should surface as a structured error, not a silent fallback to
  some default strategy.
- **Indicators:** an unknown indicator name or an out-of-bounds parameter →
  same pattern, reusing `IndicatorTerm`'s existing bounds-check logic rather
  than inventing a second, parallel validation path.
- **Regime classifier:** a date range shorter than the 252-bar lookback →
  confirm the explicit "insufficient history" result, not a quantile silently
  computed on too little data.
- **Statistics:** a zero-trade strategy fed into the significance test →
  confirm a clean rejection (e.g. "cannot test significance with zero trades"),
  not a divide-by-zero or a meaningless p-value returned as if valid.
- **Screener:** a filter combination matching zero tickers (e.g. an
  over-narrow sector + decile combination) → confirm a valid empty-result
  structure, not an unhandled exception.

This directly extends `.claude/rules/agent-honesty.md`'s "every LLM output is
validated before use; malformed content is rejected" discipline one layer
down, to the tool boundary itself — Stage 5's agent will trust these error
shapes to know when a tool call failed versus succeeded, so the failure path
needs the same manual verification as the success path, not less.

---

## Decision 1 — the significance test (locked in now, not deferred)

**Choice: a Monte Carlo permutation test against the mandatory randomized-entry
control, via `scipy.stats.monte_carlo_test`.**

Architecture.md §5 Step 3 already defines *what* the comparison is: not "did
this strategy make money" but "did it beat randomized entries at the same trade
frequency." That sentence, taken literally, already specifies a Monte Carlo
procedure — generate many alternative backtests with the same trade count and
exit logic but randomized entry timing, and see where the real strategy's
metric (Sharpe ratio) falls against that simulated distribution. The choice
below is what makes that procedure a real statistical test with a real p-value,
not just an eyeballed comparison.

**Why not a two-sample or paired t-test.** A t-test assumes the compared
quantities are approximately normally distributed and independently drawn.
Trading returns are neither: they're fat-tailed and skewed (well-documented —
crash days and gap days are far more common than a normal distribution
predicts), and they're serially correlated (volatility clustering, and any
momentum or mean-reversion effect the strategy itself is trying to exploit is,
by definition, autocorrelation). Under autocorrelation a t-test's implied
variance is too small, so its p-value is too optimistic — it would report
significance the data doesn't actually support.

**Why not a nonparametric rank test (Mann-Whitney U / Wilcoxon).** These drop
the normality assumption but still assume the individual observations being
ranked are mutually independent — autocorrelated returns violate that exactly
the same way a t-test's independence assumption is violated. They also don't
naturally express the paired, same-price-path structure the architecture's
control already specifies (same ticker, same date range, same trade count —
only entry timing varies).

**Why not a block bootstrap over the raw return series.** A block bootstrap
(resampling contiguous chunks of returns to preserve autocorrelation) is a
reasonable general-purpose fix for autocorrelation, but it would be solving a
problem the architecture's own control design already avoids. Each simulated
control isn't a resample of return data — it's a full alternative backtest run
against the real, still-autocorrelated price series, with only the entry bars
permuted. The null distribution this produces already carries the same
autocorrelation structure the real strategy experienced, because it's built
from the same underlying path, at no extra modeling cost. Introducing a
separate block-bootstrap machinery on top would be redundant with a mechanism
the control already gives for free.

**Concretely:** `scipy.stats.monte_carlo_test(data=[observed_sharpe], rvs=<callable
that runs one random-entry backtest and returns its Sharpe>, statistic=identity,
alternative='greater')`, with `rvs` implemented as a new
`backtester/strategies/random_entry_strategy.py::make_random_entry_strategy(rule,
n_trades, seed)` — a sibling to `make_rule_strategy`, reusing the same
`run_backtest` entry point, just with randomized entry bars instead of
rule-evaluated ones. This test makes **no distributional assumption at all** —
its validity comes from the resampling procedure, not an asymptotic
approximation — which is exactly what's needed given real trading returns
violate both the normality and the independence assumptions every classical
alternative relies on.

**Confidence intervals:** `scipy.stats.bootstrap`, resampled at the **trade
level**, not the daily-bar level. Stage 2's `exclusive_orders=True` means trades
from a long-only single-position strategy don't overlap in time — trade-level
returns are far closer to independent than adjacent-day returns, which would
double-count the same autocorrelated sub-path if resampled directly. Same
underlying concern as the significance test, applied to interval estimation.

**Multiple-comparisons correction:** `scipy.stats.false_discovery_control`
(Benjamini-Hochberg), exposed as a deterministic function in this stage. The
*tracking* of how many hypotheses have been tested under a charter is Stage 5's
job (it requires the agent loop and persistent state that don't exist yet) —
but the correction function itself is pure and belongs here, ready for Stage 5
to call the moment it has a count to correct against.

## Decision 2 — ticker ingestion goes through the existing Stage 1 path, not a shortcut

The screener needs more than 17 tickers to make relative (quintile/tercile/decile)
thresholds non-degenerate within a sector. Any new tickers Stage 4 adds are
ingested through:

- `data_pipeline.ingest.runner.ingest_daily(tickers)` — which internally calls
  `fetch.prices.fetch_prices` and `fetch.metadata.fetch_metadata`, both wrapped
  by `fetch.client.retry_on_failure` (tenacity: 3 attempts, exponential backoff,
  already built and tested in `tests/data_pipeline/test_retry.py`), and upserts
  through `ingest.upsert`, with per-ticker success/failure tracked in
  `IngestionRun`/`IngestionRunTicker` exactly as it is for the existing 17.
- `data_pipeline.ingest.runner.check_corporate_actions(tickers)` run afterward,
  so new tickers are corporate-actions-aware from day one (weekly-check contract,
  `.claude/rules/data-pipeline.md`), not silently exempt from the
  split/dividend handling that already protects the original 17.

**No bespoke `yfinance.download()` call is introduced anywhere in Stage 4.**
The screener itself never fetches at query time — it only filters tickers
already sitting in `TickerMetadata`/`PriceBar`; broadening the ingested universe
is a one-time build step using the same ingestion path Stage 1 already built and
gated, not a new, simplified path that would bypass retry or corporate-action
handling.

---

## Build order (bottom-up: existing wrappers first, new logic after, hardest last)

1. Dependencies + MCP scaffolding
2. Market data tool (pure wrapper)
3. Backtester tool (pure wrapper)
4. Indicators tool (one small new function + wrapper)
5. Regime classifier tool (new, self-contained, no new data needed)
6. Statistics tool (new; depends on a new backtester sibling function for the
   Monte Carlo control)
7. Screener tool (new; needs the one-time universe-broadening ingestion first)
8. Formal automated test suite across all six
9. Manual MCP verification — the actual stage gate

Per each component: explain the approach before writing (per the working
agreement), write it, then walk through it — same cycle as every prior stage.
`explanation-writer` fires after each working component and again once the
manual-verification gate passes.

## Verification

- Per-component: the new pytest suite (Component 8) run via `pytest`.
- Stage gate (Component 9): a manual verification script that starts the MCP
  server over stdio, lists its tools, and calls each — happy path and at least
  one invalid-input case — with a real ticker/date range, checked by inspecting
  the actual returned MCP response (including error cases) — not by reading
  the server code and assuming it's wired correctly.
- Nothing in this stage touches Stage 5 — no agent, no LangGraph, no LLM in any
  runtime path (the `llm_client` built in Stage 3 stays unused here, same as it
  was in Stage 3 apart from the one disclosed build-time exception).

---

## Addendum — Component 1 execution note

Built and verified against the actually-installed SDK version (`mcp==2.0.0`,
`scipy==1.18.0`), not the API surface assumed when this plan was written. The
high-level decorator API in this installed version is `mcp.server.MCPServer`,
not `mcp.server.fastmcp.FastMCP` as originally planned above — functionally
equivalent (same constructor/decorator/run shape, verified by execution
before use). Full rationale and verification trail:
`docs/explanations/stage-4/step-01-dependencies-and-mcp-scaffolding.md`.
