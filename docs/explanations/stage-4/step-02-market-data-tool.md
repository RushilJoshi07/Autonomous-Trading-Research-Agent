# Step 2 — Market Data Tool (Stage 4)

## 1. What this does

`get_price_data(ticker, start=None, end=None) -> list[PriceBarOut]` is the
first real, callable MCP tool in this project — Component 1 built the empty
server object; this component puts the first thing on it. It wraps
`backtester.data_loader.load_price_data` (built in Stage 2, unchanged since)
behind an MCP-callable interface, returning daily OHLCV bars for a ticker,
split/dividend-adjusted, straight from the cached Postgres database.

What this component is *not*: it contains no new business logic at all. It
does not fetch, does not validate a ticker beyond what `load_price_data`
already does, does not filter or transform prices. Every real decision this
tool's *output* depends on — which rows come back, what "adjusted" means,
what happens for an unknown ticker — was already made and already tested in
Stage 2. This component's entire job is making that existing, trusted
function reachable through a new protocol, without changing what it does.

---

## 2. Every meaningful line explained

### `src/mcp_tools/schemas.py`

```python
class PriceBarOut(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
```

A plain Pydantic model with no validators, describing the exact JSON shape
the tool hands back — nothing more. It lives in its own file rather than
inline in `server.py` because it's the first of several small response
models this stage will need (Component 4's indicator-series output is the
next one coming), and the project already keeps schema/model definitions
separate from the logic that uses them (`backtester/schema.py`,
`backtester/result.py` are both this same pattern). The alternative —
defining it inline above `get_price_data` in `server.py` — would work
identically for one model, but `server.py` would accumulate an unrelated
model definition for every future tool, mixing "what a tool returns" with
"how a tool is wired up" in one file with no separation between them.

### `get_price_data`'s signature

```python
def get_price_data(ticker: str, start: date | None = None, end: date | None = None) -> list[PriceBarOut]:
```

This mirrors `load_price_data`'s own signature exactly, on purpose, rather
than accepting `start`/`end` as plain strings and parsing them inside this
function. That choice rests on something checked before writing this line,
not assumed — see section 3's first decision.

```python
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
```

`SessionFactory()` is the exact object `data_pipeline/ingest/runner.py`
already uses for every DB-touching operation in this project — this line
opens a session, hands it to `load_price_data`, and the `with` block closes
it automatically once the block exits, success or failure. The alternative
of holding a module-level, long-lived session open across every tool call
was rejected without needing to write it: a session held open indefinitely
is exactly the kind of ambient, easy-to-forget-about state that has already
caused real problems elsewhere in this project's history (the AWS-profile
and OAuth-token issues documented in `docs/explanations/stage-3/step-06-llm-client.md`
section 5) — a fresh session per call is more code but has no equivalent
failure mode.

```python
    return [
        PriceBarOut(
            date=row.Index.date(),
            open=row.Open,
            high=row.High,
            low=row.Low,
            close=row.Close,
            volume=int(row.Volume),
        )
        for row in df.itertuples()
    ]
```

Converts each row of the DataFrame `load_price_data` returns into a
`PriceBarOut`. `row.Index` is `itertuples()`'s name for the DataFrame's
index value at that row — here a `pandas.Timestamp` (the DataFrame's
`DatetimeIndex`) — and `.date()` narrows it to a plain `datetime.date`,
matching `PriceBarOut.date`'s type. Why `itertuples()` rather than the more
commonly-reached-for `iterrows()` is its own decision, covered in section 3,
because it isn't a stylistic preference — a real, verified bug lived down
the `iterrows()` path.

No docstring line was skipped here: the function's one-line docstring,
`"Daily OHLCV bars for a ticker from the cached database (splits/dividends
adjusted)."`, is not decoration. This SDK's `@mcp.tool()` decorator uses a
function's docstring as its MCP-advertised `description` when no explicit
`description=` is passed — this is the exact text a future agent will read
to decide whether this tool is the right one to call, even though no agent
exists yet to read it. Getting it right now costs nothing; getting it wrong
would silently mislead Stage 5's agent later, with no compiler or test to
catch a misleading English sentence.

---

## 3. Design decisions and rejected alternatives

### Mirroring `load_price_data`'s signature, verified before assumed

The naive alternative was to accept `start`/`end` as plain `str` parameters
and parse them into `date` objects by hand inside `get_price_data` — the
safe-looking choice if you don't know how the MCP layer treats a
type-annotated parameter. Before writing the signature either way, I
registered a throwaway tool with a `d: date` parameter and called it through
the SDK with the JSON string `"2020-01-15"`. It came back as a real
`datetime.date(2020, 1, 15)` — the SDK already validates and coerces
incoming arguments against a tool's Python type hints (it builds a Pydantic
model from the function signature internally and validates the raw JSON
arguments against it, the same general mechanism `StrategyRule` uses for
rule validation, just applied to tool arguments instead of trading rules).
Given that, hand-parsing `start`/`end` as strings would have been strictly
worse: extra code, and a second place a date-parsing edge case (a malformed
string, an ambiguous format) could silently produce a wrong date instead of
a loud validation failure. Mirroring `load_price_data`'s exact signature
means the exact same `date | None` type, with `None` meaning "no bound," is
enforced identically at both layers, with nothing translated or re-parsed
in between.

**Reversibility:** trivial to change later if a future MCP client turns out
to send dates in some form this SDK's coercion doesn't handle — nothing else
in this component depends on strings never appearing here.

### The error-path re-verification — a Component 1 finding that turned out incomplete

Component 1 grepped `mcp/server/mcpserver/server.py`, found `except
Exception: return CallToolResult(..., is_error=True)`, and recorded that as
"exceptions raised inside a tool become structured errors." That finding was
directionally correct but incomplete in a way this component's own testing
caught. Calling the SDK's lower-level `mcp.call_tool(name, args, context)`
method directly — the method the grep'd exception handler actually wraps —
with a tool that raises `ValueError` did not return an `is_error=True`
result at all. It raised a real `mcp.server.mcpserver.exceptions.ToolError`
straight out of the `await` call.

The instinct to distrust here was "the earlier finding must have been
wrong." The correct instinct, and the one followed, was: re-check rather
than assume either version. Reading the surrounding code showed why both
observations are true at once. `call_tool()` — the method just tested
directly — wraps the underlying function call and re-raises any failure as
`ToolError`, with no catching of its own. `_handle_call_tool()` — a
different, higher-level method, the one the MCP protocol layer actually
invokes for a real client request — wraps a call to `self.call_tool(...)` in
its own try/except: it re-raises anything that's an `MCPError`, but converts
everything else to `is_error=True`. Whether a `ValueError` raised inside a
tool function reaches a real client as a clean structured error or an
uncaught crash depends entirely on whether `ToolError` counts as an
`MCPError` — and that's a fact about a specific class hierarchy, not
something safe to guess. Checking `mcp/server/mcpserver/exceptions.py`
directly settled it: `ToolError(MCPServerError)`, and `MCPServerError` is
declared as `class MCPServerError(Exception)` — a plain, unrelated base
class, not `mcp.shared.exceptions.MCPError`, a different class that happens
to have a very similar name in a different module. Two easily-confused
names, one letter apart in meaning, and the entire correctness of Component
9's later invalid-input verification hinges on telling them apart correctly.

Testing through `_handle_call_tool` directly (not the lower-level method
that misled the first check) confirmed the corrected understanding: a
`ValueError("No price data found for BAD")` raised inside a tool does become
`is_error=True`, with content text `"Error executing tool get_rows: No price
data found for BAD"` — the original message present, but wrapped in an
`"Error executing tool <name>: "` prefix that hadn't been anticipated before
this test. This is a small but concrete correction to how Component 9's plan
needs to check for it: **asserting the original error message as a substring
of the returned text, not as an exact match.** Getting this wrong would have
meant Component 9's own verification script failing on a correct
implementation, for a reason that had nothing to do with the tool actually
being tested — a false negative baked into the test itself, discovered only
because this component happened to re-exercise the same mechanism early,
with real data, instead of waiting for Component 9 to be the first place it
was ever tested end to end.

**What it would cost to have missed this:** Component 9's invalid-input
checks, written against the uncorrected assumption, would have failed on
every single tool the moment they were run — not because any tool was
broken, but because the test itself expected an exact string match that the
SDK never produces. That's the more expensive kind of bug to debug: a
failing test that looks like it's reporting a real defect in the code under
test, when the defect is actually in the test's own expectation.

### `itertuples()` over `iterrows()` — a real dtype bug, not a style preference

The natural first instinct for "loop over a DataFrame's rows" is
`iterrows()`. Before using it here, a small synthetic check — a two-column
DataFrame, one `float64` column and one `int64` column — showed why it was
the wrong choice for this specific row shape: `iterrows()` yields each row
as a single `pandas.Series`, and a `Series` can only hold one dtype across
all its values, so pandas silently promotes the whole row to the "widest"
common type. The synthetic test's `int64` `Volume` column came back as
`numpy.float64` inside every `iterrows()` row, purely because it shared a
row with `float64` OHLC values — no error, no warning, just a silently
wrong type. `itertuples()` does not build a combined-dtype row object at
all; it reads each column independently and preserves its real dtype, and
the same synthetic `Volume` column came back as a genuine Python `int`
through `itertuples()`.

`volume=int(row.Volume)` in the final code is still there as an explicit,
defensive cast — Pydantic's `int` field validator would likely coerce a
whole-number float safely anyway — but because `itertuples()` was already
chosen for the reason above, that cast is *confirming* a value that's
already correctly typed, not *correcting* a silent type error `iterrows()`
would otherwise have introduced one call earlier. Had `iterrows()` been used
instead, this line would have been quietly compensating for the loop's own
bug rather than being the harmless belt-and-suspenders line it actually is
— a meaningfully different, worse situation, even though the two versions
of this line look identical.

**Reversibility:** fully reversible with no downstream consequence — this is
purely an internal implementation detail of one function's row-conversion
loop.

---

## 4. Concepts introduced

**Tool argument validation as a schema boundary, one layer up from
`StrategyRule`.** Stage 3 already established the pattern of a Pydantic
model sitting at a validation boundary, rejecting malformed input before it
reaches anything that matters (`schema.py`'s `IndicatorTerm._check_indicator`
being the clearest example). This component shows the same pattern
operating one layer further out: the MCP SDK builds an equivalent validation
boundary automatically from a plain Python function's type hints, before the
function body ever runs. The practical consequence checked here is that a
malformed `date` argument from an MCP client gets rejected or coerced at
that boundary, the same way a malformed `StrategyRule` gets rejected by
Pydantic before `make_rule_strategy` ever sees it — consistent with
`.claude/rules/agent-honesty.md`'s "every LLM output is validated before
use" principle, here applying to tool call arguments rather than model
output.

**A tool call's error path vs. a protocol-level failure, now with a verified
mechanism instead of a described one.** Step 1's explainer named this
distinction conceptually. This component is where it was actually tested
against a domain error a tool genuinely raises (`ValueError` for an unknown
ticker), through the real handler a client uses, confirming the distinction
holds in practice and not just in the source code's shape.

---

## 5. How this component was tested

Two layers, matching this project's established practice of always
preferring a real execution over a plausible-sounding read of the code.

**Offline / synthetic checks**, before writing the real tool: argument
coercion (`date` string → `datetime.date`, via a throwaway tool), the
error-path mechanism (`ValueError` → `ToolError` → `is_error=True`, via a
throwaway tool, through `_handle_call_tool` specifically after the
lower-level `call_tool()` method gave a misleading first signal), and the
`itertuples()` vs `iterrows()` dtype behavior (via a two-column synthetic
DataFrame). None of these three checks touched the real database or the
real tool — they isolated exactly one mechanism each.

**Live verification against real data**, once `get_price_data` itself was
written: called through `_handle_call_tool` — the real protocol path, not
the internal shortcut used in the synthetic checks — for AAPL,
2024-01-01 through 2024-01-10, against the actual `strategy_research`
database. Got back 7 real rows with correct types (`volume` as a JSON
integer, OHLC as floats, `date` as an ISO string) and correct values (real
AAPL prices for that week). Then called it again with
`ticker="NOTAREALTICKER"` and confirmed `is_error=True` with content
`"Error executing tool get_price_data: No price data found for
NOTAREALTICKER (None – None)"` — the exact shape predicted by the earlier
synthetic error-path check, now confirmed against the real tool instead of
a throwaway stand-in.

Full existing 170-test suite run both immediately before this component's
code was written (confirming the starting baseline) and immediately after
(confirming nothing regressed) — unchanged both times.

**What this does not prove.** No automated test exists yet for this tool —
everything above was interactive verification in a `python -c` session, not
a committed, repeatable pytest test. That's consistent with the approved
Stage 4 plan's own ordering (Component 8, the formal test suite, comes after
all six tools exist, not one at a time per tool) but it is a real, honest
gap until Component 8 lands: right now, nothing would catch a future
accidental change to this function's behavior except a human noticing.

---

## 6. Interview defense

**Q: This tool does nothing `load_price_data` didn't already do. Why does
it deserve to be called a separate "component" at all?**

A: Because "wrap an existing function for a new protocol" is exactly the
kind of task that looks trivial and isn't. This component surfaced two real,
non-obvious findings that had nothing to do with `load_price_data` itself:
that the SDK's error-conversion mechanism only operates at one specific
layer (`_handle_call_tool`, not the lower-level `call_tool()`), and that a
naive DataFrame-to-JSON conversion loop can silently corrupt an integer
column's type. Both were caught here, on the simplest possible tool, before
either mistake could compound into five more tools built on the same wrong
assumption.

**Q: Why didn't you just trust Component 1's grep-based finding about the
error path instead of re-testing it here?**

A: Because a grep finds where a pattern *exists* in source code, not
whether that code path is actually the one a real caller exercises. That
gap was real here, not hypothetical: the exact method the grep found
(`_handle_call_tool`) turned out to be a different method from the one first
tested directly (`call_tool()`), and the two behave differently for the
exact case that matters — a domain exception raised inside a tool. Treating
Component 1's finding as confirmed-forever rather than confirmed-for-that-
specific-check is exactly the gap this component's re-verification closed.

**Q (hard): You're building a system whose entire premise — Sacred Gate 2,
Stage 5 — is that quantitative claims must trace to something actually
executed and checked. This component is infrastructure, not a quantitative
claim. Does the same discipline actually matter here, or is re-testing an
SDK's error handling just being overly cautious about something low-stakes?**

A: It matters here for a specific, concrete reason, not as a general
caution policy: Component 9's entire invalid-input verification section —
part of this stage's actual gate — was going to assert against the *wrong*
string shape if this component hadn't caught the "Error executing tool
<name>: " prefix first. That's not abstractly related to the project's
honesty discipline; it's the same discipline, applied to test infrastructure
instead of a trading claim. A test that asserts the wrong thing and happens
to pass is exactly as dangerous, in miniature, as a verdict that claims a
number it never actually checked — both are confident-sounding statements
resting on something that was never actually confirmed. The stakes here are
lower (nobody loses money if this component's docstring is imprecise) but
the *shape* of the failure — an assumption stated with more confidence than
it had earned — is the same one this entire project exists to guard
against.

**Honest weakness:** as noted in section 5, there is no automated test for
this tool yet. Everything verified here was real, but it was interactive and
not committed as a repeatable check. If this function's behavior regresses
before Component 8 lands, nothing in CI or `pytest` would catch it.

---

## 7. What comes next and why

Component 3 (backtester tool) is a meaningfully different kind of wrapper:
where this component's only real complexity was in the plumbing (dates,
sessions, error paths), Component 3 wraps a function whose *argument* is
itself a nontrivial, deeply nested Pydantic model (`StrategyRule`) rather
than a few scalars. Whether the coercion behavior confirmed here for a
single `date` parameter — the SDK building a validation model from type
hints and coercing JSON into it — extends cleanly to a full nested
`StrategyRule` object, or needs a different construction approach (building
the `StrategyRule` explicitly from raw arguments before calling
`make_rule_strategy`, rather than trusting the tool-argument layer to do it
implicitly), is the first real open question Component 3 has to answer,
and it wasn't resolvable from this component alone.

If this component's error-path finding were subtly wrong in some case not
tested here (a `ValueError` subclass with unusual `__str__` behavior, for
instance), the failure would most likely surface downstream as a confusing
mismatch in Component 9 — a verification check failing against real tool
output for a reason unrelated to the tool actually being broken, the same
category of false-negative this component's own re-verification prevented
once already.
