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
