# Step 9 — Manual MCP Verification (Stage 4)

## 1. What this does

This component runs `docs/architecture.md`'s own literal Stage 4 gate
criterion: "call each manually through MCP before any agent touches
them." It passed — 17 checks, all green, real subprocess, real stdio,
exit code 0. This is the last component of Stage 4; all six planned
tools now exist, are individually tested (Component 8), and have now
been confirmed callable through the actual protocol a real client will
use, not an approximation of it.

The one thing this component is *not*, and the distinction that gives it
its whole reason to exist: every component from 2 through 8 already
called these tools interactively, many times, using `MCPServer`'s own
`_handle_call_tool()` method invoked directly, in-process. That method is
real code — it's the actual handler `_handle_call_tool` documented and
relied on since Component 1 — but calling it directly, in the same Python
process, from the same script that also imports the server module, never
exercises the thing that actually makes MCP a *protocol*: a separate
process, real bytes flowing over stdio pipes, real JSON-RPC serialization
and deserialization, a real client performing the MCP initialization
handshake before it's allowed to call anything. Every one of those layers
is a place a defect could exist that nine rounds of in-process calling
would never have found. This component is the first and only place in
Stage 4 that actually exercises all of them, together, for real.

---

## 2. Every meaningful line explained

### Launching the server as a genuine subprocess

```python
params = StdioServerParameters(
    command=os.path.abspath(".venv/bin/python3"),
    args=["-m", "mcp_tools.server"],
    cwd=os.getcwd(),
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
```

`StdioServerParameters` describes how to *launch* the server — as a
separate OS process running `python -m mcp_tools.server`, not as a
Python object already sitting in memory. `stdio_client` starts that
process and returns the read/write ends of its stdio pipes; wrapping
them in a `ClientSession` and calling `await session.initialize()` is
the actual MCP handshake — the same negotiation a real client (Stage 5's
LangGraph client, eventually) has to complete before it's allowed to list
or call a single tool. None of Components 2 through 8's own verification
ever ran this handshake at all.

`cwd=os.getcwd()` turned out to be the one thing this launch genuinely
needs — see section 3 for what was tried first and ruled out.

### The tool-registration check, before any individual tool is exercised

```python
tool_list = await session.list_tools()
found = {t.name for t in tool_list.tools}
record(
    "all 9 tool functions registered on the server",
    found == _EXPECTED_TOOLS,
    ...
)
```

Checked once, up front, separately from any individual tool's own
happy-path check. A tool that silently failed to register at all —
a typo in a decorator, an import-time exception in one tool's module
that somehow didn't crash the whole server — would not necessarily be
caught by "call each tool and check the result," since a missing tool
simply wouldn't be called; this check exists specifically to catch that
category of failure, which none of the per-tool checks below could.

### Reusing Component 8's own impossible-rule fixture, not reinventing it

```python
impossible_rule = {
    "name": "impossible",
    "description": "RSI can never cross below -100 -- structurally 0 trades",
    "entry": {"kind": "leaf", "comparison": {
        "left": {"kind": "indicator", "name": "RSI", "params": {"length": 14}},
        "op": "crosses_below", "right": {"kind": "constant", "value": -100},
    }},
    "exit_after_bars": 5,
}
```

The identical construction `tests/research_stats/test_significance.py`
already uses to force a real, structurally-guaranteed zero-trade result
(`RSI` is mathematically bounded to `[0, 100]`, so `crosses_below -100`
can never fire). Reused verbatim here rather than re-derived, since it's
already a proven, understood way to reach this exact error path — writing
a second, independently-constructed "impossible" rule for this script
would have been redundant effort risking a second, subtly different
edge case rather than testing the one already known to work.

### Distinguishing a real error from a valid, notable edge case

```python
short = await session.call_tool("classify_regime", {"ticker": TICKER, "end": "2010-02-01"})
short_rows = short.structured_content.get("result", []) if not short.is_error else []
starts_insufficient = len(short_rows) > 0 and short_rows[0]["trend_regime"] == "insufficient_history"
record(
    "classify_regime short-history edge case -> explicit insufficient_history, not an error",
    not short.is_error and starts_insufficient,
    ...
)
```

This check's PASS condition requires `is_error` to be `False` — the
*opposite* assertion from every other edge-case check in this script.
`classify_regime` on a date range near the very start of AAPL's ingested
history isn't a failure; Component 5 designed it to report
`"insufficient_history"` explicitly as a successful, structured result,
not to raise. The same distinction applies to `screen_universe`'s
empty-sector check below. Treating either of these as an `is_error=True`
case would have meant testing against a behavior neither tool was ever
designed to have.

---

## 3. Design decisions and rejected alternatives

### Real subprocess and stdio, not the in-process shortcut every other component relied on

This is the one decision the entire component exists to make, so it's
worth stating plainly why the alternative — reusing `_handle_call_tool()`
in-process, the way Components 2 through 8 all did — was never a real
option here, even though it's exactly what every prior interactive check
this stage used. That shortcut proves a tool's own logic is correct; it
cannot prove anything about the transport layer sitting between that
logic and a real caller, because it never touches that layer at all — no
process boundary, no serialization, no handshake. Architecture.md's own
Stage 4 gate wording ("call each manually *through MCP*") is specifically
about that layer, not the tool logic underneath it, which Component 8's
formal suite already covers thoroughly. Running the real transport at
least once, deliberately, is what turns "the tools are correct" (already
established) into "the tools are correct *and reachable exactly the way
a real client will reach them*" (this component's actual, distinct
contribution).

### `cwd`, not `PYTHONPATH` — investigated, not assumed

The first version of this script's launch configuration included an
explicit `PYTHONPATH` override, on the reasonable-sounding assumption
that a subprocess needs to be told where to find `mcp_tools`,
`backtester`, and the rest. Before committing to that, it was tested
directly: importing `mcp_tools.server` from a completely different
working directory (`/tmp`), with no `PYTHONPATH` set at all, succeeded —
confirming the project's editable install (`pip install -e .`, done back
in Component 1) already makes every package importable from anywhere,
with no path trick required. The import failed one line later, inside
`data_pipeline.config`'s settings construction, with a plain "field
required: `database_url`" — not a Python import error at all, but
`pydantic-settings`'s default `env_file=".env"` being a *relative* path,
resolved against whatever the process's working directory happens to be,
and `/tmp` has no `.env` file. That isolated the actual requirement
precisely: not `PYTHONPATH`, just `cwd` pointing at the project root.
The final script sets only `cwd` — simpler than the first, defensively
over-specified version, and verified to be sufficient rather than assumed
adequate on the strength of a plausible-sounding first guess.

### Reusing the same tickers, rules, and known numbers every prior component already established

Every happy-path check in this script uses `AAPL`, `SMA_CROSSOVER`, or a
p-values list already used and verified in an earlier component
(Component 3's 44-trade backtest, Component 4's SMA(10) 23-row result,
Component 6's own p-value list). This was a deliberate choice, not a lack
of imagination: a check whose expected shape is already independently
known from an earlier component's own verification is a stronger check
than one exercising fresh, never-before-seen numbers — a discrepancy here
would be immediately recognizable as a regression (a real transport-layer
defect breaking something already proven correct at the logic layer)
rather than requiring fresh investigation into whether the new number was
ever right to begin with.

---

## 4. Concepts introduced

**The distinction between "the logic is correct" and "the logic is
reachable the way it will actually be reached."** These are genuinely
different claims, and this stage's own structure makes the difference
concrete rather than abstract: Component 8 spent an entire component
formally proving the first claim, function by function, with real
assertions and a real defect found and fixed along the way. This
component exists entirely to establish the second claim, which nothing
in Component 8 — or any interactive check before it — actually touched.
A system can have the first without the second (correct code, unreachable
or subtly mis-served through its actual interface) and a formal test
suite alone would never catch that gap, because a test suite, by
construction, calls functions directly rather than through whatever
transport sits in front of them in production.

---

## 5. How the verification gate was satisfied

The gate itself, run for real: `.venv/bin/python scripts/verify_stage4_gate.py`,
exit code 0, 17/17 checks passed. Every one of the six conceptual tools
the approved Stage 4 plan describes is represented — `indicators`
registers two functions (`compute_indicator`, `list_indicators`) and
`statistics` registers three (`test_significance`, `confidence_interval`,
`correct_p_values`), so the six conceptual tools become nine individually
registered MCP tool functions, matching Component 1's own single-server,
multiple-tools topology decision.

Real results, not placeholders: `get_price_data` returned 7 real AAPL
rows for a real date range; `run_backtest` reproduced the exact known
44-trade `sma_10_30_crossover` result Component 3 first established;
`compute_indicator` reproduced Component 4's own 23-row `SMA(10)` result
exactly; `test_significance` returned a real, valid p-value (0.032 on
this run, at `n_resamples=30` for a faster gate check than the 300
default) — small, comfortably significant, and consistent with Component
8's fix rather than the pre-fix, now-known-wrong ≈0.33; `screen_universe`
found the same 9 real Technology-sector tickers Component 7's own
verification found. Every invalid-input check (unknown ticker, malformed
rule, unknown indicator, a structurally zero-trade rule, a too-short
window for a bootstrap CI) correctly produced `is_error=True` through the
real protocol path — not just the in-process shortcut already confirmed
for each of these individually in earlier components. Both edge cases
that are valid results rather than errors (`classify_regime`'s
`insufficient_history`, `screen_universe`'s empty group) came back
exactly as their own components designed them to.

**What this does not prove.** This script ran once, in a single local
environment, with a single client implementation (the official Python
SDK's own `ClientSession`) — it says nothing about how a *different*
MCP client implementation (Stage 5's actual LangGraph integration, not
yet built) will behave against this same server, only that a
standards-compliant client using this SDK's own client library can. It
also does not test concurrent tool calls, connection loss and recovery,
or any transport beyond stdio (`sse`, `streamable-http` are unused and
untested, consistent with Component 1's own scoping decision to stay
local through Stage 7). And every check here still runs against the real
`strategy_research` production database, the same one every interactive
check this stage has used — this script has no isolated test-database
equivalent of its own, unlike Component 8's formal suite.

---

## 6. Interview defense

**Q: Why didn't this verification happen earlier, as part of Component 8's
formal test suite, instead of as its own separate, final component?**

A: Because they're testing genuinely different things, and conflating
them would have weakened both. Component 8's suite runs against an
isolated test database, with synthetic data, specifically so it's fast,
deterministic, and safe to run on every change without touching real
data — properties a pytest suite needs. This component deliberately runs
against the real subprocess-and-stdio transport and the real production
database, specifically because that's what the architecture document's
gate actually asks for: not "the logic is correct in isolation" (Component
8's job) but "the whole system is reachable exactly the way a real caller
will reach it" (this component's job). Merging them would have meant
either slowing down Component 8's suite with real subprocess launches on
every test run, or weakening this component's own guarantee by running it
against synthetic data that never proves anything about the real
deployment path.

**Q: Why does the tool-registration check assert an *exact* set match
(`found == _EXPECTED_TOOLS`) instead of just checking that the expected
tools are present?**

A: Because an unexpected *extra* tool is also a real finding worth
catching, not just a missing one. A stray tool registered by accident —
a leftover from a rejected design, a duplicate under a slightly different
name — would silently expand what a future agent could call, without ever
having been decided on deliberately. An exact-match check treats "more
tools than expected" and "fewer tools than expected" as equally worth
surfacing, rather than only checking for the failure mode that's more
obviously bad.

**Q (hard): This script hardcodes real production data (`AAPL`,
specific date ranges, specific known result values) directly into a gate
that's meant to run before any agent exists. What happens when Stage 5's
agent starts calling these same tools against tickers and date ranges
this script never anticipated — is there any reason to believe this gate
generalizes beyond the exact cases it happens to check?**

A: Not on its own, and that's worth stating plainly rather than implying
broader coverage than what actually exists. This gate proves the
transport layer works correctly for the specific inputs it exercises; it
says nothing structural about *why* it would keep working for a different
ticker or a different rule shape — that confidence comes entirely from
Component 8's formal suite, which does test the underlying logic more
generally (parametrized over multiple tickers, multiple rule shapes,
synthetic edge cases). This component's actual contribution is narrower
and specific: confirming the transport layer itself — the part Component
8's suite structurally cannot exercise, since it never launches a real
subprocess — doesn't silently corrupt or misroute what the (separately,
thoroughly tested) logic underneath it produces. The two components
together, not either alone, are what the phrase "verified" actually
means for this stage.

**Honest weaknesses, stated plainly:** this script ran once, manually,
in one environment — it isn't part of any continuous or repeated check,
so a regression introduced after this point in the transport layer
specifically (as opposed to the tool logic, which Component 8's suite
would catch on every run) would not be caught automatically. It exercises
exactly one client implementation. And, as noted in section 5, it runs
against real production data rather than an isolated test database,
which was a deliberate choice for what this component needed to prove but
does mean it's not something that could be run carelessly or frequently
without being mindful of that.

---

## 7. What comes next and why

Stage 4 is complete. All six planned tools — backtester, market data,
indicators, regime classifier, statistics, screener — exist, are
individually correct (Component 8's formal suite, 220 tests), and are
now confirmed reachable through the real protocol a future agent will
actually use (this component). Per `docs/architecture.md`'s build order,
Stage 5 — the agentic core: LangGraph, the execution loop, tiered RAG,
loop guardrails — is next, and it is the first stage in this entire
project where an LLM enters the runtime path at all. Everything built in
Stages 1 through 4, this stage very much included, exists specifically so
that when Stage 5's agent starts deciding which tool to call next, every
tool it can reach is already known to be correct and already known to be
reachable — the agent's own job becomes choosing and reasoning, not
discovering that the ground underneath it was never solid.

A separate, Level-3 synthesis document — `stage-4-summary.md` — still
needs to be written as a distinct follow-up to this file, covering how
all nine of this stage's components fit together, what the gate as a
whole proved (and didn't), and the stage-level interview defense. That
synthesis is deliberately not attempted here; this file's job was Component
9 alone, in the same depth as every step file before it.

If anything Stage 4 built turns out to be subtly wrong in a way none of
its own verification caught — the untested tight-gap real-data behavior
from Component 8 being the most concrete named candidate — the most
likely place it would first become visible is not in this stage's own
tests or this component's own gate script, both of which already passed.
It would surface in Stage 5, the first time an agent's own reasoning
depends on a number this stage produced, and that number turns out not to
mean what Stage 4 believed it meant. That is precisely the shape of risk
the project's own architecture document names as the hardest problem
this whole system exists to solve — and precisely why Stage 5's own
sacred gate treats an agent's willingness to distrust and kill a result,
not just accept whatever a tool hands it, as the harder of its two
halves.
