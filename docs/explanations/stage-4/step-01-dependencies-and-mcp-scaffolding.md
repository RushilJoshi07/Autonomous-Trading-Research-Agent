# Step 1 — Dependencies and MCP Scaffolding (Stage 4)

## 1. What this does

This component adds the two new dependencies Stage 4 needs (`mcp`, the
official Model Context Protocol Python SDK, and `scipy`, already a settled
choice in `docs/architecture.md` §7 but not yet an installed dependency) and
creates an empty scaffold, `src/mcp_tools/server.py`, holding a single server
object with zero tools registered on it.

What this component is *not*: nothing here is callable yet in any way an
agent — or even a human — could meaningfully use. No tool wraps
`run_backtest`, no tool reads a price bar, nothing touches `ALL_INDICATORS`.
This is pure plumbing, the same role `llm_client` played at the start of
Stage 3 Component 7: infrastructure proven to import and run cleanly, built
before anything real depends on it, so that every later component in this
stage builds on a foundation already known to work rather than discovering a
foundational problem midway through Component 4 or 5.

---

## 2. Every meaningful line explained

### `pyproject.toml`

```toml
"mcp>=1.0",
"scipy>=1.11",
```

Two new entries in `dependencies`, both floors chosen deliberately rather than
copied from habit.

`scipy>=1.11` is not an arbitrary "recent enough" number — it is the actual
binding constraint of three functions Stage 4's later statistics tool
(Component 6) needs: `scipy.stats.false_discovery_control` (the
Benjamini-Hochberg multiple-comparisons correction the approved Stage 4 plan
commits to) did not exist before SciPy 1.11. `scipy.stats.monte_carlo_test`
and `scipy.stats.bootstrap` — the other two functions that plan also commits
to — are both older, so they don't tighten the floor further. Stating the
floor this way, rather than as "whatever's current," means a future reader
can trace *why* 1.11 specifically without re-deriving it from scratch.

`mcp>=1.0` is deliberately loose. I don't have reliable knowledge of exact
current release numbers for this SDK past my training cutoff, and pinning a
precise-looking minor version I couldn't actually verify would be worse than
an honest floor — it would look more certain than it is. The honest move was
to let `pip install -e .` resolve to whatever's actually current and then
verify what showed up, which is exactly what section 3 below does.

### `src/mcp_tools/__init__.py`

Empty, deliberately matching `backtester/__init__.py` and
`data_pipeline/__init__.py` — both are empty files that exist only to make
their directory a package. No reason to deviate from an established, working
convention for a file whose only job is to exist.

### `src/mcp_tools/server.py`

```python
from mcp.server import MCPServer

mcp = MCPServer("agentic-finance-platform")

if __name__ == "__main__":
    mcp.run()
```

Three lines, each earning its place. The import and the module-level `mcp =
MCPServer(...)` object are what every later component (2 through 7) will
import and decorate with `@mcp.tool()` — this is the single point of
convergence the approved plan's "one server, six tools" decision described.
The `if __name__ == "__main__":` guard is what makes `python -m
mcp_tools.server` (or the equivalent stdio-launch invocation Component 9's
verification script and, eventually, Stage 5's LangGraph MCP client will
both use) actually start the server, while still letting the module be
imported elsewhere (e.g., by tests) without side effects — importing this
module must not, by itself, start blocking on stdin waiting for MCP
messages, or every future test that imports anything from `mcp_tools` would
hang.

`mcp.run()` with no explicit `transport` argument relies on the SDK's own
default. This was not left as an unverified assumption — section 3 below
covers what was actually checked about this SDK version's behavior before
relying on it.

---

## 3. Design decisions and rejected alternatives

### The API surface: `MCPServer`, not `FastMCP`

The approved Stage 4 plan, written before any dependency was actually
installed, specified `mcp.server.fastmcp.FastMCP` — the name and import path
this SDK's high-level decorator API has used for most of its public life,
and the name I expected to find. The first attempt to import it failed:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

The rejected approach here was guessing a fix — trying `from mcp import
FastMCP`, or some other plausible-looking variation, and iterating by trial
and error until something imported. That would have meant relying on
pattern-matching against a remembered API shape for a dependency that had
just been freshly resolved to a specific, unfamiliar version (`mcp==2.0.0`)
by pip, with no guarantee the remembered shape still applied.

What was actually done: inspect the installed package's real contents
directly. `find .venv/lib/python3.13/site-packages/mcp/server -maxdepth 1`
listed a `mcpserver/` subdirectory; reading `mcp/server/__init__.py` showed
`from .mcpserver import MCPServer` in its exports. Rather than assume this
was a compatible replacement from the name alone, I verified it by execution
— registered a throwaway `@mcp.tool()` function on a real `MCPServer`
instance and called it directly in a `python -c` snippet, confirming the
constructor-takes-a-name / decorator-registers-a-function / decorated-
function-still-directly-callable shape all still held before writing a
single line into `server.py`. This is the same "verify by execution, don't
trust a remembered claim or a plausible guess" discipline
`stage-3-summary.md` names as the throughline of every real bug Stage 3
found — applied here to an SDK surface instead of indicator math, and
catching the wrong assumption in a five-line diagnostic instead of a failed
import three components later, when `server.py` would have far more riding
on it.

**What it would cost to be wrong here anyway:** very little, structurally.
Every later component only ever touches `server.py` through `mcp.tool()`
decorators and the module-level `mcp` object — if this SDK's API surface
changes again in a future upgrade, the fix is entirely contained to this one
three-line file. That containment is itself a reason the "one file, one
convergence point" scaffolding decision (from the approved plan) is worth
having, independent of the topology reasoning that motivated it originally.

### Verifying the error-surfacing mechanism now, even though nothing calls it yet

Component 9 of the approved plan — several components away — depends on a
specific claim: that an exception raised inside a `@mcp.tool()` function
surfaces to a caller as a structured, catchable error rather than a crash or
a hang. That claim was written into the plan based on general knowledge of
how this class of SDK behaves, before this specific installed version had
been inspected at all.

Rather than leave that claim unverified until Component 9 actually needs it
— by which point four more components' worth of work would depend on the
same SDK, with no confirmation this specific mechanism still worked the way
assumed — I checked it now, while already inspecting this package for the
`FastMCP`-vs-`MCPServer` question above. `grep`ping
`mcp/server/mcpserver/server.py` for exception handling found the exact
mechanism at lines 423-424: a caught `Exception` becomes `CallToolResult(
content=[TextContent(type="text", text=str(e))], is_error=True)`. The
mechanism holds. One concrete detail worth recording now so it isn't a
surprise later: the Python attribute is `is_error` (snake_case) in this SDK
version, not `isError` — Component 9's verification script needs to check
the correct attribute name, not the wire-protocol JSON's camelCase spelling.

The alternative here was deferring this check to Component 9, on the
reasoning that "it's not needed yet." That would have been consistent with
not doing unnecessary work early, but it would have let an unverified
assumption sit load-bearing under a design decision (the plan's explicit,
detailed invalid-input verification requirements) for the entire span of
Components 2 through 8, discoverable only right at the stage gate — the
worst possible time to discover a wrong assumption, after everything else
is already built on top of it.

---

## 4. Concepts introduced

**MCP (Model Context Protocol) transport.** MCP servers can communicate over
different transports — `stdio` (the server is a subprocess; the client
writes to its stdin and reads its stdout) is the one this project uses,
versus `sse` or `streamable-http` (the server runs as a real network
service). The approved plan commits to stdio specifically because these
tools run locally, launched as subprocesses by whatever calls them — a
manual verification script now, LangGraph's own MCP client starting in
Stage 5 — with no need for a listening network port, matching
`docs/architecture.md` §8's "stay local through Stage 7" cost-discipline
guidance. `mcp.run()`'s default transport is what this project relies on;
Component 9's verification script will be the first thing to actually
exercise that transport end to end.

**A tool call's error path vs. a protocol-level failure.** MCP distinguishes
two different kinds of "something went wrong": a transport or protocol
failure (the server crashed, the connection dropped) versus a tool
returning `CallToolResult(is_error=True, ...)` — a structured, expected
outcome meaning "the tool ran, and it determined the input or request was
invalid." The latter is what every domain validation error in this stage
(an unknown ticker, a malformed `StrategyRule`, an out-of-bounds indicator
parameter) is expected to produce. This distinction matters because it's
exactly the boundary Stage 5's agent will need to reason about: a
`CallToolResult` with `is_error=True` is legible, structured feedback the
agent can read and act on (the same category of thing
`.claude/rules/agent-honesty.md` requires for every LLM output — malformed
content rejected, not silently accepted); an actual crash or hang is not.

---

## 5. How the verification gate was satisfied

This component doesn't yet reach Stage 4's real gate — "each tool callable
manually through MCP" (Component 9) — because there are no tools registered
yet to call. Two narrower things were verified here instead, both by
execution rather than assumption:

1. **The scaffold imports cleanly.** `python -c "from mcp_tools.server import
   mcp"` (with `src` on the path, matching `pyproject.toml`'s
   `[tool.pytest.ini_options] pythonpath = ["src"]`) succeeds and returns a
   real `MCPServer` instance.
2. **Nothing regressed.** The full existing 170-test suite
   (`tests/backtester/`, `tests/data_pipeline/`) still passes unchanged after
   this component's `pyproject.toml` edit and the new package's addition —
   confirming this component, despite adding new top-level dependencies with
   a fair amount of their own transitive dependencies (`starlette`,
   `uvicorn`, `cryptography`, `jsonschema`, none of which this project
   actually uses directly yet — see section 6's honest-weakness note),
   touched nothing that could have broken Stage 1-3's work.

**What this does not prove.** That the `MCPServer`/`@mcp.tool()` shape
verified with a throwaway `ping` function in a `python -c` snippet
generalizes correctly to a real tool wrapping a Pydantic model, a database
session, or a `pandas.DataFrame` return value. Those are exactly what
Components 2 through 7 will each test for real, one at a time, as they're
built.

---

## 6. Interview defense

**Q: Why not just add `mcp` and `scipy` to `pyproject.toml` and move on —
why does adding two dependency lines and an empty server object deserve its
own step explainer?**

A: Because what actually happened wasn't "add two lines" — it was
discovering, by checking rather than assuming, that this project's actual
installed SDK version doesn't match the API surface the approved plan and my
own working knowledge both assumed, and fixing that before it became a
failed import three components downstream. The two-line dependency change
was trivial; confirming what those two lines actually installed, and
whether the code built against them would work, was the real content of
this component.

**Q: You installed `mcp==2.0.0` — a version whose exact API you didn't know
in advance. How do you know the *rest* of this SDK's surface (the parts
Components 2 through 9 will actually use — argument schemas, return content
types, the stdio transport itself) hasn't also changed in ways not yet
discovered?**

A: I don't, and that's an honest residual risk rather than a solved problem.
What this component establishes is a *method* for closing that gap safely
as it's hit — inspect the installed package directly, verify by running
real code against it, don't proceed on a remembered API shape — rather than
a guarantee that no more surprises exist. The `FastMCP` → `MCPServer`
rename and the `isError` → `is_error` naming detail are both concrete
instances of exactly this happening once already, in the very first
component that touched this SDK. It would be overconfident to claim there
can't be a third instance waiting in, say, how tool arguments get typed and
validated (Component 3's `StrategyRule`) or how a `pandas.DataFrame` needs
to be shaped for an MCP response (Component 2). The honest position is:
expect more of these, verify each one when it's actually hit, the same way
this one was.

**Q (hard): This SDK pulled in `starlette`, `uvicorn`, and `cryptography` —
none of which this project uses, since Stage 4 only uses the stdio
transport. Doesn't that undercut the "stay local, minimal footprint"
cost-discipline argument the plan itself made for choosing stdio over a
network transport?**

A: Partially, and it's worth stating plainly rather than glossing over.
Those packages exist because `mcp` is a general-purpose SDK supporting
multiple transports and an auth layer this project will never touch — they
add install size and dependency-resolution surface, not runtime cost or
security exposure, since nothing in this project ever imports or exercises
`mcp.server.auth` or the HTTP transport modules. The stdio-vs-network
transport decision was about *runtime* footprint (no listening port, no
always-on process, matching §8's actual warning about idle infrastructure
billing) — it was never a claim about minimizing what gets installed into
`.venv`. A dependency that's merely present but never imported at runtime is
a real but much smaller cost than one that's actually running.

**Honest weakness:** the scaffold has exactly one integration test right
now — a throwaway `ping` function that never made it into the actual
codebase, only into a terminal session. `server.py` itself has zero direct
test coverage yet, because there's nothing on it worth testing until a real
tool is registered. That's consistent with the plan's own component
ordering (Component 8, the formal test suite, comes after all six tools
exist), but it does mean this component's only current defense against
regression is "the full existing suite still passes" — which, by
construction, cannot catch a defect in code the existing suite never
imports.

---

## 7. What comes next and why

Component 2 (market data tool) is the first real test of this scaffold: a
single `@mcp.tool()` function wrapping `backtester.data_loader.
load_price_data`, returning real OHLCV rows shaped for an MCP response for
the first time. If anything about today's verified shape — the decorator,
the constructor, the `is_error` error path — doesn't actually generalize to
a function with real parameters, a database session, and a `pandas.
DataFrame` return value, Component 2 is where that would surface, immediately
and loudly (an import error or a shape mismatch), not silently. That's the
preferable failure mode this project has consistently favored over a
plausible-looking wrong result — and it's a direct consequence of building
the scaffold first and verifying it in isolation, rather than writing
Component 2's real tool against an unverified assumption about the API it's
built on.
