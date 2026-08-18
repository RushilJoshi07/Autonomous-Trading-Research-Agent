# Stage 4 Summary — Tools via MCP

## 1. What this stage does

Stage 3 closed with a complete, working backtesting product reachable only
by direct Python import. Stage 4 is what makes that product — and five
capabilities that didn't exist before this stage at all — reachable through
MCP, the protocol Stage 5's agent will actually use to call them. Six tools
now exist behind one MCP server: market data, the backtester, indicators,
a regime classifier, a statistics module, and a screener. Three of them
(market data, backtester, indicators) are thin wrappers over Stage 2/3 code
already proven correct; three (regime classifier, statistics, screener) are
genuinely new domain logic, built this stage, each with its own real design
questions this document's component files each resolve in full.

`docs/architecture.md`'s own framing for this stage is direct: every tool
the agent will ever call is deterministic, already-tested Python — the
agent's job, starting Stage 5, is choosing and reasoning about what these
tools report, never computing anything itself. What exists now that didn't
before this stage: a real MCP interface to all of it, formally tested (220
automated tests, Component 8), and manually confirmed reachable through the
actual protocol a real client uses, not an in-process approximation of it
(Component 9, this stage's own gate, passed).

What this stage is not: there is still no agent. Nothing built in Stage 4
reasons, plans, or decides — every one of its nine components is either a
deterministic function, a thin protocol wrapper around one, or a
verification script exercising both. The one exception this project has
ever disclosed to "no LLM before Stage 5" remains Stage 3's own bounded,
closed case (extended-indicator bounds proposal); Stage 4 adds nothing to
that exception and needed nothing from it.

---

## 2. The nine components, and where to read each one deeply

Synthesis only below — each component's full "why this and not the
alternative" treatment lives in its own step file, not repeated here:

1. Dependencies, MCP scaffolding, and the six-tools-one-server topology decision — [step-01-dependencies-and-mcp-scaffolding.md](step-01-dependencies-and-mcp-scaffolding.md)
2. `get_price_data` — [step-02-market-data-tool.md](step-02-market-data-tool.md)
3. `run_backtest` — [step-03-backtester-tool.md](step-03-backtester-tool.md)
4. `compute_indicator` / `list_indicators`, the first new domain logic this stage — [step-04-indicators-tool.md](step-04-indicators-tool.md)
5. `classify_regime`, this project's first lookahead-safety proof outside the backtester — [step-05-regime-classifier-tool.md](step-05-regime-classifier-tool.md)
6. `test_significance` / `confidence_interval` / `correct_p_values`, the largest component, and the one whose own claim was later found wrong — [step-06-statistics-tool.md](step-06-statistics-tool.md) (see its own addendum, added during Component 8)
7. `screen_universe`, plus the real-database gap this component discovered and closed — [step-07-screener-tool.md](step-07-screener-tool.md)
8. The formal test suite, and the calibration defect it found and fixed in already-shipped code — [step-08-formal-test-suite.md](step-08-formal-test-suite.md)
9. Manual MCP verification — this stage's actual, literal gate — [step-09-manual-mcp-verification.md](step-09-manual-mcp-verification.md)

---

## 3. Cross-component decisions — the threads that run through the whole stage

**"Structural correctness over calendar or probability estimation" is one
design instinct, applied at three different layers of this stage, not
three independent good ideas.** Component 5's `classify_regime` needed
roughly 252 prior bars of history to label even the first bar a caller
requests — the tempting fix was padding the database query with an
estimated calendar-day buffer (252 trading days ≈ 400 calendar days, with
slack for weekends and holidays); the actual fix loads a ticker's entire
available history and filters the *output* to the requested window only
afterward, a structural guarantee rather than an estimated one. Component
7's screener needed the identical discipline one layer up, at universe
selection instead of per-bar labeling: its lookback window uses a
row-count `LIMIT`, not a calendar-day estimate, for the same reason.
Component 8's anchored random-entry mechanism is the same instinct at a
third, more consequential layer still — the tempting fix for a saturated
trade-count calibration was tuning the entry probability empirically;
the actual fix pairs each control trade to one of the real strategy's own
historical exit bars, a structural guarantee that doesn't depend on
getting a probability calibration right for data it hasn't seen yet. None
of these three fixes were arrived at independently — each one, once named,
became the obvious first candidate for the next.

**"Verify by execution, not by trusting a remembered or plausible-sounding
claim" is the single most load-bearing discipline this stage practiced,**
continuing the exact throughline `stage-3-summary.md` already named as
running through every real bug Stage 3 found. It appears in nearly every
component this stage built: Component 1 discovering the plan's assumed
`FastMCP` API didn't exist in the actually-installed SDK, and verifying the
real `MCPServer` API with a throwaway tool before writing a line of real
code; Component 2 re-verifying Component 1's own error-path finding at the
correct layer, after the first, less-precise finding turned out incomplete;
Component 3 confirming the SDK's argument coercion handled a deeply nested,
discriminated-union `StrategyRule` before trusting it, rather than
extrapolating from a single scalar case; Component 6 catching a real
`scipy.stats.monte_carlo_test` bug (a tuple-shaped `size` argument, not a
plain int) specifically *because* an earlier offline toy check had been too
permissive to expose it; Component 8 investigating the real mechanism
behind a saturated calibration — a sparse, fixed exit-signal calendar, not
a few outlier-long trades — before designing any fix; Component 9
confirming the client-side result object's attribute naming and the real
subprocess-launch requirements with direct round trips rather than
assumption. This is not incidental repetition. It is the specific
discipline that found this stage's single most consequential defect
(Component 8's calibration fix) and prevented at least four smaller ones
from ever reaching a commit.

**"Disclose a gap rather than hide it or silently solve it partway" recurs
as the stage's second load-bearing thread**, the same survivorship-bias-
style honesty `docs/architecture.md` §6 already commits to at the data
layer, now applied to tool design. Component 5's `"insufficient_history"`
label, reported explicitly rather than silently omitted. Component 6's
`null_mean_trades`/`null_std_trades` fields, added specifically so a
structural guarantee (the anchored mechanism's exact trade-count match) is
empirically checkable per call rather than merely asserted in a docstring.
Component 7's `group_size`, reported once and prominently so a thin sample
can't dress up a decile-level claim as more meaningful than it is, and its
explicit disclosure that sector/industry metadata is *not* point-in-time
even though the computed metrics now are. Component 8's addendum to
`step-06-statistics-tool.md`, documenting a correction to an
already-written, already-approved explanation in place, rather than
quietly rewriting history to look as though the mistake never happened.

**`BacktestResult` accumulated provenance fields incrementally across this
stage exactly the way `stage-3-summary.md`'s own synthesis predicted it
would** — "starts as a nice-to-have, ends up load-bearing." `trade_returns`
(Component 6) existed for one purpose (trade-level bootstrap confidence
intervals) and `exit_bars` (Component 8) for a second, unrelated one
(anchoring random-entry controls to real historical exit points) — neither
field could have been anticipated as necessary when `BacktestResult` was
first designed in Stage 2, and both turned out to be genuinely required,
not merely convenient, the moment the component that needed them was
actually built.

**Every component that touched already-committed code — from an earlier
component in this same stage, or from an earlier stage entirely — flagged
it explicitly before writing, and waited for confirmation.** Component 1's
recommendation to deviate from `docs/architecture.md`'s literal "each tool
is an MCP server" wording (one server, six tools, not six processes) was
presented with full reasoning and approved before any server code existed.
Component 5's refactor of Component 4's own `indicator_compute.py` was
raised and justified against Component 4's own, different duplication
decision before being written. Component 6's larger refactor of Stage 3's
`rule_strategy.py` — extracting four shared helpers so two structurally
parallel strategy compilers could exist — was explained and approved before
a line of it was written, with the user's own framing ("making the two
strategy types structurally parallel is what makes the statistical
comparison actually valid, not just tidy code") becoming the standard the
rest of the component was held to. `BacktestResult`'s two extensions this
stage (`trade_returns`, `exit_bars`) both touched Stage 2/3's own
`result.py`, and both were flagged the same way. Nothing in this stage
silently modified a decision made before it.

---

## 4. Concepts spanning multiple components

**Lookahead bias, structurally prevented one layer up from the backtester,
for the first time in this project.** Sacred Gate 1 (Stage 2) proves the
backtester itself never lets a simulated trade see future data. This stage
is where the identical concern first showed up in tools that never place a
trade at all: Component 5's trailing (never expanding, never full-sample)
252-bar rolling window for regime labels, verified structurally by
truncating data and confirming an earlier bar's own classification doesn't
change; Component 7's `as_of`-gated screener metrics, verified with a real,
concrete demonstration — the same tickers, ranked by the same metric,
producing a genuinely different ranking depending only on what date a
caller claims to be screening from. Both are the same underlying concern
`docs/architecture.md` §5 names explicitly for universe selection
("screening on today's data, backtesting from 2015 uses future
information"), now proven closed at two different layers rather than left
as a documented risk.

**A tool's own claim about its behavior is not the same thing as a
verified fact about it — the entire justification for Component 8 existing
as a separate, later component rather than being folded into each tool's
own build.** Component 6 shipped with an honestly-stated, reasonable-
sounding design claim ("expected trade count approximates the target, not
an exact per-draw guarantee") that had never actually been checked against
adversarial numbers. Component 8's own test, written specifically to check
that claim rather than merely re-confirm an existing impression, found it
substantively false for an entire category of rule — not imprecise,
*saturating*, a qualitatively different failure a looser or more generous
test would never have surfaced. This is the clearest evidence this stage
produced that a formal, adversarial test suite is a genuinely different
kind of verification from interactive spot-checking, not a slower,
less-urgent version of the same thing.

---

## 5. How Stage 4's gate was satisfied — exhaustively

Stage 4's gate, per `docs/architecture.md`'s own Stage 4 row, is stated
plainly and is not one of the project's two sacred gates (Stage 2's
no-lookahead proof, already passed; Stage 5's fabrication and
hypothesis-killing proof, not yet reached) — it is its own, separate,
explicitly scoped criterion: **"call each manually through MCP before any
agent uses it."** Nothing in this stage's own scope required more than
that, and nothing here claims more than that was achieved.

**What Component 9 actually did.** Every interactive check performed by
Components 2 through 8 — dozens of them, across this entire stage — used
`MCPServer._handle_call_tool()` invoked directly, in-process: the same
Python process, no subprocess boundary, no real stdio bytes, no real
JSON-RPC serialization, no client handshake. That shortcut is a completely
valid way to prove a tool's own logic is correct, and it's what every prior
component in this stage relied on for exactly that purpose. It never once,
across eight components, exercised the actual transport a real MCP client
uses. Component 9 is the first and only place in this stage that launched
the real server as a genuine OS subprocess and drove it with the actual
client SDK (`mcp.ClientSession`, `mcp.stdio_client`) over real stdio —
performing the real MCP initialization handshake, sending real serialized
requests, receiving real deserialized responses.

**The result:** 17 checks, spanning all 9 individually-registered MCP tool
functions (the six conceptual tools the plan describes; `indicators`
registers two functions, `statistics` registers three), each exercising
both a happy path and at least one invalid-input or edge case, all
17/17 passing, script exit code 0.

**What the gate proves.** That a standards-compliant MCP client, using the
official SDK's own client library, can reach every one of this stage's six
tools through the real transport, receive correctly-shaped structured
results for valid input, and receive correctly-shaped, clearly-labeled
error results for invalid input — including correctly distinguishing a
genuine failure (`is_error=True`, for an unknown ticker, a malformed rule,
an unknown indicator, a structurally zero-trade significance test, a
too-short confidence-interval window) from a valid-but-notable edge case
that is *not* a failure (`classify_regime`'s explicit
`"insufficient_history"`, `screen_universe`'s valid empty result for an
unmatched sector).

**What it does not prove**, stated as plainly as Stage 3's own gate
section stated its limits. This script ran once, manually, in one
environment, against one client implementation — it says nothing about how
a *different* MCP client (Stage 5's actual LangGraph integration, not yet
built) will behave against this same server, only that a
standards-compliant client using this SDK's own library can. It exercises
stdio only, not `sse` or `streamable-http` (unused and untested,
consistent with Component 1's own "stay local through Stage 7" scoping). It
does not test concurrent tool calls or connection loss and recovery. It
runs against the real production database, the same one every interactive
check this stage relied on, not an isolated equivalent of Component 8's
own test-database suite. And critically, it proves the *transport* is
correct for the specific inputs it happens to exercise — the confidence
that this generalizes to inputs it doesn't exercise comes entirely from
Component 8's separately-run, more broadly parametrized formal suite, not
from this gate on its own. The two together, not either alone, are what
"Stage 4 is verified" actually means.

---

## 6. Interview defense — the stage as a whole

**Q: What's the single most defensible claim you can make about Stage 4?**

A: That the stage's own formal test suite (Component 8) found and fixed a
real, previously-shipped, previously-approved defect in this stage's own
work — not a defect discovered by luck or by a user complaint, but by
deliberately writing a real test for a claim that had only ever been
checked interactively before. The defect wasn't cosmetic: it changed
`test_significance`'s actual reported statistical conclusion for the exact
real-data case this stage had already used as its own worked example,
from "not significant" (p≈0.33) to "significant at the 1% level"
(p≈0.0099). Finding and fixing that, inside this same stage, before any
agent existed to have acted on the wrong number, is the strongest evidence
this stage's own verification discipline actually works rather than just
being asserted to.

**Q: Why didn't Stage 4 build a formal test suite alongside each tool,
component by component, instead of deferring all of it to Component 8?**

A: Because the two kinds of verification this stage performed are
genuinely different, and interleaving them would have weakened both.
Every component's own interactive verification (real calls, real data,
including deliberately-broken inputs) is fast, immediate, and specific to
the exact case being built — it's what let each component's own design be
confirmed correct before the next one was started on top of it. A formal
suite needs an isolated test database, synthetic and parametrized data,
and a broader sweep of cases than any one component's own interactive
check would naturally cover — properties worth building once, deliberately,
rather than piecemeal. The actual cost of deferring it showed up exactly
once, and it was worth paying: Component 8's dedicated focus on writing
*real* tests, not just re-confirming what interactive checks had already
suggested, is precisely what surfaced the calibration defect. A test
suite built incrementally, one component at a time, under the same time
pressure as each component's own build, might well have written a weaker,
more confirmatory version of that exact test — one that would have passed.

**Q (hard): Component 6 shipped a statistics tool, verified against real
data, approved by the user, with a p-value that Component 8 later proved
wrong — not imprecise, wrong, in the specific direction of understating
significance. If this tool had been the one thing standing between a
research claim and a real decision, that decision would have been made on
a false negative. How do you defend having shipped something with that
defect, even temporarily?**

A: By being precise about what "shipped" means at this point in the
project, not by minimizing the defect itself. No agent exists yet to have
acted on the original, wrong 0.33 — every use of this tool so far,
including that number, was this project's own verification exercise, never
a conclusion presented to anyone as a finding. The honest defense isn't
that the defect was harmless in some absolute sense — a tool with this
specific flaw, reachable by a real agent before this fix landed, would have
systematically understated significance for a real and identifiable
category of rule, which is a serious class of error to have shipped
uncaught even briefly. The actual defense is structural: this project's
entire build order — six deterministic tools, individually verified, then
formally tested, then manually gated through the real protocol, all of it
*before* Stage 5 gives any of it to an agent that could act on it — exists
specifically so a defect like this gets found and fixed at exactly the
point it was found, not after. Component 8 catching this here, before
Stage 5, is that build order doing its job. It would be a materially
weaker defense if this had been caught in Stage 6's evaluation harness, or
worse, in a real research verdict.

**Honest weaknesses, held across the whole stage rather than restated per
component:** the tight-gap skip in Component 8's anchored mechanism has
only ever been exercised against a deliberately constructed synthetic
example, never a real rule on real data that happened to trigger it. The
probability-based calibration's own remaining imprecision for
`exit_after_bars`-only rules (well-behaved, not saturating, but not exact
either) was measured once and not investigated further — a smaller,
undiscovered version of Component 6's original problem is a real
possibility there, simply not one this stage's test tolerance was set
tight enough to catch if it exists. Component 9's gate ran once, manually,
against one client implementation, with no mechanism yet to catch a future
regression in the transport layer specifically, the way Component 8's
suite would catch one in the tool logic. And several individual
components' own step files disclose narrower gaps specific to their own
scope — the `liquidity`/`as_of` combination never independently tested in
Component 7, the extended-indicator and cross-check paths never exercised
in Component 4 — each real, each named in its own file, none of them
papered over here for the sake of a cleaner stage-level summary.

---

## 7. What comes next and why

Stage 5 — the agentic core: LangGraph, the execution loop, tiered RAG
grounding, loop guardrails — is next, per `docs/architecture.md`'s build
order, and it is the first stage in this entire project where an LLM
enters the runtime path in any capacity beyond Stage 3's single, closed,
disclosed exception. Everything Stage 4 built exists specifically so that
Stage 5's agent inherits a tool layer already known correct and already
known reachable, rather than discovering either fact the hard way while
also trying to reason about what to do next. The loop `docs/architecture.md`
§5 Step 4 describes — decide, call a tool, write the result to state,
repeat — depends completely on every tool in that loop behaving exactly as
this stage's verification says it does; Stage 5's own difficulty is meant
to be building a skeptical, hypothesis-killing reasoner on top of a solid
foundation, not discovering the foundation was never solid.

If something Stage 4 built is subtly wrong in a way none of its own
verification caught — the untested tight-gap real-data behavior, or the
unmeasured `exit_after_bars` calibration precision, being the two most
concrete named candidates — the most likely place it first becomes visible
is not this stage's own tests or gate, both of which already passed
cleanly. It surfaces in Stage 5, the first time an agent's own reasoning
depends on a number this stage produced, and that number turns out not to
mean what Stage 4 believed it meant. That is exactly the shape of risk
`docs/architecture.md` names as the hardest problem this entire project
exists to solve, and exactly why Stage 5's own sacred gate treats an
agent's willingness to distrust and kill a plausible-looking result — not
merely avoiding a crash — as the harder of its two halves. Stage 4's own
calibration defect, found and fixed inside this same stage rather than
carried forward into Stage 5 undiscovered, is the clearest evidence yet in
this project that the discipline Stage 5 will need already works.
