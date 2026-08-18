# Commit log — Stage 4

Lightweight notes after each commit — what changed, why, anything non-obvious.

---

## Stage 4 component 1: dependencies and MCP scaffolding

**Change:** Added `mcp` and `scipy` to `pyproject.toml` (scipy floored at
1.11, the version that introduced `false_discovery_control`, needed later
for the statistics tool). Added `src/mcp_tools/__init__.py` and
`server.py` — an empty MCP server object, no tools registered yet. Saved
the approved Stage 4 plan to `docs/plans/stage-4-plan.md`.

What is non-obvious: the plan assumed `mcp.server.fastmcp.FastMCP`; the
actually-installed `mcp==2.0.0` doesn't have that module at all. Found and
verified the real API (`mcp.server.MCPServer`, same decorator/run shape) by
inspecting the installed package and running a throwaway tool through it
before writing `server.py` — not by guessing. Also pre-verified, because a
later component's plan depends on it, that this SDK version still converts
an exception raised inside a tool into a structured error result
(`is_error=True`, snake_case in this version). Full trail:
`docs/explanations/stage-4/step-01-dependencies-and-mcp-scaffolding.md`.
Full 170-test suite confirmed unchanged.

---

## Stage 4 component 2: market data tool

**Change:** Added `src/mcp_tools/schemas.py` (`PriceBarOut`) and registered
`get_price_data(ticker, start=None, end=None)` on the MCP server — a thin
wrapper around Stage 2's `load_price_data`, unchanged. First real,
end-to-end-tested tool in this stage.

What is non-obvious: Component 1's error-path finding turned out incomplete
— the SDK's `is_error=True` conversion only happens in `_handle_call_tool`
(the real protocol handler), not the lower-level `call_tool()` method,
which instead raises `ToolError`. Re-verified the correct layer directly
and found the error text is wrapped as `"Error executing tool <name>:
<message>"` — Component 9's invalid-input checks need a substring match,
not exact. Also found `iterrows()` silently upcasts the integer `Volume`
column to `float64` when mixed with float OHLC columns; used `itertuples()`
instead, confirmed with a synthetic test before writing the real loop. Full
trail: `docs/explanations/stage-4/step-02-market-data-tool.md`. Verified
against real AAPL data and a real invalid-ticker call through the actual
protocol handler; full 170-test suite confirmed unchanged.

---

## Stage 4 component 3: backtester tool

**Change:** Added `run_backtest(rule, ticker, start=None, end=None,
commission=None, cash=None) -> BacktestResult` — wraps `load_price_data`,
`make_rule_strategy`, and `engine.run_backtest` (aliased `_run_backtest` to
avoid shadowing) exactly as Stage 2/3 built them. `commission`/`cash`
default to `None` and are only forwarded if set, keeping `engine.py`'s own
constants the single source of truth. `trade_on_close` deliberately not
exposed — it exists for one narrow Stage-2 lookahead-testing scenario, not
anything a `StrategyRule`-compiled strategy needs.

What is non-obvious: confirmed the SDK's argument coercion (only proven for
a scalar `date` in Component 2) also handles `StrategyRule`'s full nested,
recursive, discriminated-union structure — and that a malformed rule
(unknown indicator, three levels deep) is rejected before the tool body
even runs, with the full precision of the underlying validator's message
and field path preserved. Full trail:
`docs/explanations/stage-4/step-03-backtester-tool.md`. Verified end-to-end
with a real backtest (SMA(10/30) crossover, AAPL, 2015-2024: Sharpe 0.678,
44 trades) and a real malformed-rule error; full 170-test suite confirmed
unchanged.

---

## Stage 4 component 4: indicators tool

**Change:** Added `backtester/indicator_compute.py` (`compute_indicator`,
the first genuinely new domain logic this stage, reusing `IndicatorTerm`
for validation and Stage 3's own `normalize_params`/`select_output_column`
unchanged) and two new MCP tools, `compute_indicator` and `list_indicators`.
New `IndicatorValueOut`/`IndicatorInfo` response schemas.

What is non-obvious: `_FIELD_TO_COLUMN` deliberately duplicates
`rule_strategy.py`'s `_FIELD_TO_ATTR` rather than being refactored into a
shared constant — discussed explicitly with the user first, since it looks
like it contradicts Component 3's commission/cash dedup but isn't the same
risk (fixed external naming convention vs. a business-decision number that
can drift). `NaN` rows are filtered in the MCP wrapper, not the pure
function, since that's a JSON-shaping concern, not a fact about the
computation. Full trail:
`docs/explanations/stage-4/step-04-indicators-tool.md`. Verified
end-to-end: `list_indicators` returned 222 entries (29 core, matching
Stage 3's own documented count exactly); `compute_indicator(AAPL, SMA,
length=10)` correctly dropped exactly its 9-bar warm-up period; both an
unknown-indicator and an out-of-bounds-parameter error surfaced correctly.
Full 170-test suite confirmed unchanged.

---

## Stage 4 component 5: regime classifier tool

**Change:** Refactored `indicator_compute.py` to split out
`compute_indicator_series(df, name, params)` from `compute_indicator`
(discussed first — unlike Component 4's `_FIELD_TO_COLUMN` duplication,
this is real multi-line logic that would otherwise need 3 copies, not a
stable constant). Added `backtester/regime.py` (`classify_regime`: ADX for
trend, NATR over raw ATR for volatility, 252-bar trailing rolling
percentile, tercile labels with an explicit `neutral` band) and the
`classify_regime` MCP tool, plus `RegimeRecordOut`.

What is non-obvious: the MCP tool always loads a ticker's *entire*
history internally and filters the output to the caller's requested
window only afterward — never forwards `start` into `load_price_data` the
way every other tool this stage does — because the rolling computation
needs ~252 prior bars to label even the first requested bar correctly.
Considered and rejected an estimated calendar-day buffer instead; loading
everything is structurally correct rather than approximately correct.
Also found the real insufficient-history boundary lands at bar 264, not
252 — ADX(14)'s own ~13-bar warmup compounds with the 252-bar rolling
window. Full trail:
`docs/explanations/stage-4/step-05-regime-classifier-tool.md`. Verified
end-to-end: a 2024-start request against AAPL's decade-plus history
returned real labels immediately (proving the fix works); the full-history
transition was found exactly at index 263→264. Full 170-test suite
confirmed unchanged (checked once after the refactor alone, then again
after the full component).

---

## Stage 4 component 6: statistics tool

**Change:** Largest component this stage. Added `trade_returns` to
`BacktestResult` (Stage 2/3's `result.py`). Refactored `rule_strategy.py`
to extract `unique_terms`/`indicator_usage`/`wire_indicators`/`apply_exit`
as shared helpers (discussed first — needed so a random-entry control can
share exit logic and indicator wiring with the real strategy exactly,
making the statistical comparison valid, not just tidy). Added
`backtester/strategies/random_entry_strategy.py` (Bernoulli per-bar entry,
calibrated to the real strategy's trade count in expectation). Added new
`research_stats/` package: `significance.py` (Monte Carlo permutation test
via `scipy.stats.monte_carlo_test`, with a capped retry-on-zero-trades
guard against `NaN`-corrupting the null distribution), `confidence.py`
(trade-level bootstrap CI via `scipy.stats.bootstrap`), and
`multiple_comparisons.py` (Benjamini-Hochberg via
`scipy.stats.false_discovery_control`). Added `test_significance`,
`confidence_interval`, `correct_p_values` MCP tools.

What is non-obvious: `scipy.stats.monte_carlo_test` passes `size` to `rvs`
as a tuple, not a plain int — a real bug from an offline toy check that
happened to work by accident (`numpy`'s `size=` accepts both), caught only
when the real function was tested for real. Revised `n_resamples` down
from the plan's original guess of 999 to 300 after measuring real
backtest cost (~93ms), then found the real default run took 4.7s in
practice — much faster than even the revised estimate. Full trail:
`docs/explanations/stage-4/step-06-statistics-tool.md`. Verified
end-to-end against real AAPL data: `test_significance` reproduced
Component 3's known Sharpe (0.678) and returned a defensible p-value
(≈0.33 — SMA crossover doesn't convincingly beat random entries on AAPL's
strong uptrend); `confidence_interval` returned a real 44-trade CI; both
error paths (zero real trades, too-few-trades CI) verified, one requiring
a deliberately-constructed impossible rule to actually reach. Full
170-test suite confirmed unchanged (checked after the refactor alone and
after the full component).

---

## Stage 4 component 7: screener tool

**Change:** Last of the six planned tools. Discovered (not caused) that
only `AAPL` among the original 17-ticker `universe.py` list had ever
actually been ingested — the other 16 had zero rows, zero metadata, zero
run history. Flagged immediately; with the user's go-ahead, ingested 37
new tickers plus the 16 missing originals through the existing Stage 1
path (0 failures), bringing the real total to 54. Rewrote `universe.py`
from a static list to `all_tickers(session)`, a live `TickerMetadata`
query; updated `cli.py` (the standing nightly-refresh entry point) to use
it. Added `data_pipeline/screener.py` (`screen`: sector/industry filters
via SQL, liquidity/volatility ranked by percentile within the matched
group, `_MIN_OBSERVATIONS`-gated exclusion) and the `screen_universe` MCP
tool.

What is non-obvious: the screener's central design point is point-in-time
correctness via `as_of` — a `date <= as_of` filter and a row-count
`LIMIT` (not a calendar-day estimate), the same discipline as Component
5's `classify_regime`, one layer up at universe selection instead of
per-bar. Sector/industry metadata is explicitly *not* point-in-time
(`TickerMetadata` has no history) — disclosed, not hidden, same pattern
as the survivorship-bias gap. Simplified the response mid-design: dropped
six planned boolean quintile/tercile/decile flags in favor of just the
raw percentile, since the full ranked group already makes every cut
recoverable. Full trail:
`docs/explanations/stage-4/step-07-screener-tool.md`. Verified end-to-end,
including the specific proof requested: Technology-sector volatility
during the COVID crash vs. a calm 2024 period showed every ticker's
volatility roughly doubling AND the ranking itself shifting (INTC fell
from 1st to 3rd; AAPL and MSFT swapped relative order) — real evidence
`as_of` does load-bearing work, not unused plumbing. Full 170-test suite
confirmed unchanged.
