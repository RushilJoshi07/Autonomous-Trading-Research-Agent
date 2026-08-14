# Stage 3 Summary — Strategy Schema, Two-Tier Indicators, and the Gate

## 1. What this stage does

Stage 2 delivered a backtesting engine that could run exactly one thing:
`SMACrossover`, a hardcoded `backtesting.py` `Strategy` subclass. The
product this whole project is building is an agent that translates *any*
strategy expressed in plain English into something executable — a fixed menu
of one hardcoded strategy contradicts that promise on its face. Stage 3 is
what closes that gap: a general, compositional rule format
(`StrategyRule`/`Condition`/`Comparison`/`Term`) that any indicator-based or
price-pattern-based strategy can be expressed in, a registry of ~150
pandas-ta indicators that rules can reference (29 hand-verified, ~127
auto-generated and execution-verified), a pure evaluator that turns a rule
into a bar-by-bar true/false decision, and an interpreter that compiles any
validated rule into a real `backtesting.py` `Strategy` — with zero
strategy-specific Python code written per strategy. Four worked examples
(`KNOWN_STRATEGIES`) prove the schema is actually expressive enough to
represent real, cited literature strategies, including a multi-bar
candlestick pattern (morning star) that exercises corners of the schema the
three indicator-driven strategies never touch.

`docs/architecture.md`'s own framing for this milestone is direct: **"End of
Stage 3 = a complete working backtesting product."** That claim is not
aspirational anymore — Stage 3's gate (section 5 below) has passed against
real market data, not just synthetic. What exists now that didn't exist
before Stage 3: a user (or, starting Stage 5, an agent) can express *any*
indicator-based or candlestick-pattern-based strategy as data — a
`StrategyRule` — and get a real, cost-aware, no-lookahead backtest back,
without a single line of new Python.

What this stage is **not**: there is still no agent. Nothing in Stage 3 (or
any stage before it) reasons about which strategy to try, generates a
hypothesis, or decides anything — every one of Stage 3's 9 components is a
deterministic function or a one-time, disclosed, offline build script. The
one narrow exception — extended-indicator bounds proposal — is covered
exhaustively in section 4 below, precisely because it is the one place this
stage's "no runtime LLM" claim needed a real, careful boundary rather than a
blanket assertion.

---

## 2. The nine components, and where to read each one deeply

This document is synthesis, not a re-walk — each component's full "why this
and not the alternative" treatment lives in its own step file, and none of
that is repeated here:

1. Dependencies (pandas-ta, anthropic) — informal, no dedicated step file.
2. `indicators.py`'s core registry — [step-01-indicator-registry.md](step-01-indicator-registry.md)
3. `schema.py`'s `StrategyRule` and `KNOWN_STRATEGIES` — [step-02-schema.md](step-02-schema.md)
4. `evaluator.py`'s pure condition evaluation — [step-03-evaluator.md](step-03-evaluator.md)
5. `strategies/rule_strategy.py`'s compiler — [step-04-rule-strategy.md](step-04-rule-strategy.md)
6. `BacktestResult` provenance fields — [step-05-provenance-fields.md](step-05-provenance-fields.md)
7. `llm_client`'s minimal LLM abstraction — [step-06-llm-client.md](step-06-llm-client.md)
8. Extended indicator generation and verification — [step-07-extended-indicators.md](step-07-extended-indicators.md)
9. The formal test suite completion (plan §8) — [step-08-test-suite-completion.md](step-08-test-suite-completion.md)
10. The gate script (plan §9) — [step-09-gate-script.md](step-09-gate-script.md)

(Alembic's baseline, this stage's final piece, is infrastructure
housekeeping rather than a component with its own "why this and not the
alternative" story — covered in section 5 below, not given its own step
file, a deliberate scoping call made explicitly when that work was planned.)

---

## 3. Cross-component decisions — the threads that run through the whole stage

**The two-tier registry is one design decision spanning two components four
steps apart.** Component 2 established the shape (`IndicatorSpec`, `tier`,
`verified`) with 29 hand-picked, hand-verified entries. Component 8, five
components later, is what makes that `tier` field earn its keep: ~127 more
entries, auto-generated and execution-verified rather than hand-picked. The
alternative — hand-verifying all ~150 pandas-ta indicators one at a time, the
way the first 29 were — was never seriously on the table; it would have
taken weeks for marginal indicators nobody has asked for yet, for a product
whose actual bottleneck is agent reasoning quality (Stage 5), not indicator
count. The alternative on the other end — trusting an LLM's claim about an
indicator wholesale, no verification — was rejected even more firmly,
because it would have meant admitting fabricated claims into the exact
registry `schema.py`'s validator treats as ground truth. The tier field is
what let both extremes be avoided: a fast, cheap, LLM-assisted *proposal*
step, gated by a slow, thorough, deterministic *verification* step, with
`verified: bool` as the one flag that actually controls what a rule can use
(`schema.py`'s `IndicatorTerm._check_indicator` refuses anything
`verified=False`, unconditionally).

**"Verify by execution, never trust an unverified claim" is not one
component's rule — it's the thread every real bug in this stage was found
by.** Component 2 found `bbands`' dead `std` parameter this way. Component 5
found the indicator dict-storage bug this way — comparing a `len()` at a
named attribute against a `len()` at a dict key and getting 45 vs. 500, not
by reading the code and reasoning about it. Component 8 found the same dead-
parameter pattern recurring at scale (a dozen more indicators, not just
`bbands`), plus three genuinely new failure modes execution alone could
surface: a coincidental digit-prefix collision, a function with a
parameter-dependent output shape, and one function (`ichimoku`) whose return
type isn't even a `Series`/`DataFrame`. The gate script (plan §9) found the
`rsi_14_30_70` trade-count question this way too — not by trusting either
the pipeline's number or a hunch about what "should" happen, but by building
a second, fully independent implementation of the same rule in plain Python
and confirming it produced the identical number. Nine components, five
distinct real bugs, one method for finding all of them: run the thing and
check, don't reason from assumption.

**Provenance (`indicators_used`/`extended_indicators_used`) is a thread that
starts as a nice-to-have and ends up load-bearing.** Component 6 added the
fields to `BacktestResult` mostly because Component 5 had already computed
the data and it seemed wasteful to let it disappear. By Component 8, those
same fields are what makes it *provable* — not just claimed — that a rule
compiled through a verified extended indicator, rather than crashing or
silently falling back to something else (`test_verified_extended_indicator_populates_provenance_on_result`
exists specifically to guard this). By the gate script, `extended_indicators_used`
being empty on all four `KNOWN_STRATEGIES` results is itself informative —
it confirms the four literature strategies never depended on anything but
the hand-verified core set, which is exactly what should be true for
strategies whose literature predates this project's own indicator-generation
pipeline by decades.

**"Prove the test isn't vacuous" is a discipline that had to be learned once,
the hard way, and was then applied deliberately everywhere after.**
Component 5's dedup regression test initially passed even with its target
bug deliberately reintroduced — a false negative, caught only because it was
checked both ways. Every subsequent test-writing effort in this stage
treated that as the standard, not the exception: plan §8's positive-offset
tests were verified to genuinely fail (with the *right* kind of failure, not
just any failure) when `validate_offset`'s check was monkeypatched out across
all three modules that independently import it; Component 8's adversarial
verifier tests prove the verification pipeline both rejects a genuinely dead
parameter *and* accepts a genuinely valid one, not just one half of that
claim. This is arguably this stage's single most reusable lesson for Stage
5, whose entire second sacred gate is "does the agent kill bad hypotheses" —
the exact same shape of question, at a much higher stake.

**The LLM boundary was drawn once, precisely, and then defended against
drift.** `CLAUDE.md` and `docs/architecture.md` originally stated "Stages 1–3
use no LLM at all" — a blanket claim that Component 8 made literally false
the moment `generate_extended_indicators.py`'s first real call went out.
Rather than let that inaccuracy sit (the handoff document flagged it as
outstanding for a full session before it was fixed), the claim was corrected
in five separate locations across both files — three prose statements and
two build-order table cells — specifically worded so the correction *closes*
the exception rather than establishing it as a precedent: "Stage 3 made one
disclosed exception... do not add another LLM call before Stage 5's loop
guardrails exist." The boundary Stage 5 will actually need to respect —
no LLM decides a quantitative outcome, every LLM output is validated before
use, code disposes even when a model proposes — was exercised for real here,
under real constraints (Bedrock inference-profile IDs, forced tool-use,
batched calls with per-chunk fault isolation), before Stage 5 has to build
the harder version of the same discipline under a live agentic loop.

---

## 4. Concepts spanning multiple components

**Structural verification vs. trusting a claim.** The single concept this
whole stage keeps re-deriving in different contexts: a system that lets an
unverified claim (an LLM's guess about a parameter, a raw crossing count that
looks like it should imply a trade count, a bounds table that hasn't been
independently checked) reach a decision point is a system waiting to be
subtly wrong in a way nobody notices until much later. Every verification
mechanism this stage built — `IndicatorSpec.verified`, the sensitivity test,
`KNOWN_DEVIATIONS`'s required independent-reproduction reason string — is the
same concept applied at a different layer.

**The bounded, disclosed exception, as a reusable pattern, not a one-off.**
Two completely different problems in this stage were solved with
structurally the same mechanism: Component 8's amendment to "no LLM before
Stage 5" (one narrow, dated, precisely-scoped exception, closed rather than
open-ended) and the gate script's `KNOWN_DEVIATIONS` (one narrow, dated,
independently-verified exception per specific bound, with an explicit
accumulation limit forcing review rather than silent tolerance). Neither
problem was solved by loosening a rule to make an inconvenient result go
away — both were solved by making the exception itself a first-class,
visible, bounded thing.

**Provider-specific infrastructure constraints as recurring, not one-off,
friction.** Bedrock's cross-region inference-profile requirement (a model
can't always be invoked by its bare foundation-model ID) hit twice in this
stage, for two different models — Sonnet in Component 7, Haiku in Component
8 — and both times the fix was the same discipline: query the real account
(`list_inference_profiles` / `aws bedrock list-inference-profiles`) rather
than guess at a naming pattern from the first instance. A concept worth
naming plainly for anyone extending this project to a new model: assume
nothing about Bedrock model-ID conventions carries over between models
without checking.

---

## 5. How Stage 3's gate was satisfied — exhaustively

Stage 3 does not have one of the project's two **sacred** gates (those are
Stage 2's no-lookahead proof and Stage 5's fabrication/hypothesis-killing
proof, per `CLAUDE.md`) — but it does have `docs/architecture.md`'s own
stated gate criterion, "literature-consistent results," plus
`docs/plans/stage-3-plan.md`'s own explicit verification checklist, which
this section covers item by item.

**Schema + known strategies.** All four `KNOWN_STRATEGIES` — SMA(10/30)
crossover, RSI(14) 30/70, RSI(2) 10/90, and morning star — construct and
validate through `schema.py`. Morning star specifically exercises
`BodyTerm`/`MidpointTerm`/`RangeTerm`/`ScaledTerm`, none of which the other
three strategies need at all — proof the schema's expressiveness isn't
accidentally narrower than the four examples that happen to use it.

**Literature-consistent results — the actual Stage 3 gate.** Run for real
against real AAPL daily bars, 2015-01-01 through 2024-12-31 (already
ingested by Stage 1; this stage only reads it), all four strategies compiled
via `make_rule_strategy` and executed via `run_backtest`, checked against
bounds independently drawn from each strategy's own cited literature source.
Result: **3 of 4 clean, 1 via a single, formally disclosed, independently
verified exception** (`rsi_14_30_70`: 12 trades against a literature floor of
20 — reproduced exactly by a standalone plain-Python simulation built
entirely outside this codebase's own pipeline, confirming the shortfall is a
real property of AAPL's unusually persistent 2015–2024 uptrend interacting
with long-only single-position semantics, not a defect). This is the single
most load-bearing empirical result in the entire stage, and it is disclosed
honestly here rather than rounded up to "4 for 4": the gate passed with one
disclosed, understood, and bounded exception, not a clean sweep.

**Formal automated regression coverage.** 170 tests, all passing, covering
every piece this stage built: 29 core indicators at both declared bounds,
every `StrategyRule` validation case the schema defines, pure evaluator
logic (comparisons, crossovers, NaN handling, Sacred-Gate-1-style positive-
offset rejection — tested at both the isolated-evaluator layer and through
the full compiled pipeline), the interpreter's dedup and provenance
behavior, and the extended-indicator verification pipeline's own core
correctness claim (rejects broken, accepts valid, proven both directions).

**Alembic baseline.** Both `strategy_research` (prod, holding 4,164 real
price rows) and `strategy_research_test` databases stamped at a single
baseline revision. The baseline migration was confirmed empty — `pass` in
both `upgrade()` and `downgrade()` — by reading the generated file before
applying anything, since both databases' live schema was created directly
from the same SQLAlchemy models this baseline diffs against; there was no
real drift to capture, only a starting point to record for future schema
changes to build on. `stamp head`, not `upgrade head`, was used deliberately
(an empty migration applied via `upgrade` to a genuinely fresh, empty
database would create zero tables) — and this choice doubles as the
documented fresh-setup sequence in `CLAUDE.md`'s previously-empty `Commands`
section, so there is exactly one true setup path, not two that could drift
apart. All three setup commands (`createdb`, `init_db.py`, `stamp head`)
were checked for idempotency empirically, not assumed — `createdb` was
confirmed to fail cleanly (not silently no-op) on a second run against an
existing database, `create_all()` was confirmed safe to re-run against the
real, populated prod database, and `stamp head` was confirmed to be a clean
no-op on a second run in both databases.

**What Stage 3's gate does *not* prove**, stated plainly rather than
glossed over: it proves the pipeline is correct on one ticker (AAPL), one
decade (2015–2024), and one data source (Stage 1's cache). It says nothing
about a different asset class, a bear-market regime this specific decade
didn't include, or the survivorship-bias coverage gap already disclosed at
the data layer (unrelated to and unaffected by this stage). It proves these
four strategies' *mechanics* are computed correctly — not that they are good
trading ideas, which was never Stage 3's question to answer.

---

## 6. Interview defense — the stage as a whole

**Q: What's the single most defensible claim you can make about Stage 3?**

A: That every quantitative claim in this stage traces to something that was
actually executed and checked, never assumed. That's true at every scale
this stage operated at — a single dead parameter caught by varying it and
comparing outputs, a systemic indicator-verification pipeline that rejects
on any doubt, and a real gate result on real market data where an
unexpected number was investigated with an independent reproduction before
any conclusion was drawn, let alone any bound adjusted.

**Q (hard): You closed this stage with one disclosed gate exception
(`rsi_14_30_70`, 12 trades vs. a floor of 20) rather than a clean pass. Why
is that an acceptable way to close a stage, rather than a sign the stage
isn't actually done?**

A: Because "acceptable" here doesn't mean "ignored" — it means specifically:
independently verified as correct (not a guess, not a rationalization), the
underlying mechanism fully explained (long-only single-position semantics
interacting with one ticker's unusually persistent trend), and recorded in a
way that can't quietly disappear or accumulate (`KNOWN_DEVIATIONS`, keyed to
the exact bound, with a mandatory reproducible reason string, and a hard
limit — 3 — on how many such exceptions this gate will tolerate before it
refuses to report a clean pass at all, regardless of how well any individual
one is argued). A gate that can only ever report "clean" or "broken" would
have forced a worse outcome here: either silently loosening a
literature-derived bound to manufacture a clean pass (the same overfitting
failure mode this project's own architecture document warns against for the
screener's thresholds), or declaring the whole gate failed over a result
that real investigation shows isn't actually a defect. Neither is more
honest than what actually happened.

**Q: Why didn't you just build the extended-indicator registry by hand,
the same way the core 29 were built?**

A: Scale and opportunity cost. Hand-verifying ~150 pandas-ta functions one
at a time — reading each one's docs, constructing a synthetic case, checking
its parameters actually do something — is exactly what Component 2 did for
29 of them, and it worked, but it doesn't scale to the other 120+ without
costing real weeks for indicators nobody has asked for yet. The two-tier
design gets the coverage without that cost: code determines everything it
provably can (inputs, multi-output structure, parameter existence) and an
LLM proposes only the one thing code genuinely cannot determine (reasonable
numeric bounds) — and nothing from that proposal is trusted until it's been
executed and checked exactly as rigorously as the hand-built 29 were. The
project's actual bottleneck was never indicator coverage; it's Stage 5's
agent reasoning quality, and this stage's job was to not become the thing
that blocked getting there.

**Honest weaknesses, stated plainly:**
- The gate ran on one ticker and one decade. A pipeline bug specific to a
  different volatility regime or asset class would not be caught by
  anything built in this stage.
- `test_evaluator.py`'s crossover coverage is asymmetric — `crosses_above`
  has both a true-flip and a false-already-above test; `crosses_below` only
  has the true-flip case (documented explicitly in step-08's own interview
  defense, not discovered here for the first time).
- The extended-indicator sweep's 66 rejected candidates are a conservative
  lower bound on what's usable, not a ceiling — nobody has gone back to ask,
  for each one, whether a smarter check could rescue it. That's real
  headroom left on the table, not a defect, but it's headroom nonetheless.
- One native process crash (`SIGTRAP`) occurred during Component 8's
  development and was resolved by removing its apparent trigger
  (`ichimoku`'s incompatible return type), but the precise underlying
  mechanism was never fully confirmed. Disclosed honestly in step-07 rather
  than claimed as fully understood.

---

## 7. What comes next and why

Stage 4 wraps this stage's tools — the backtester, the indicator registry,
the screener (not yet built), the regime classifier (not yet built), the
statistics module (not yet built) — as MCP servers, each independently
callable before any agent ever touches them. Everything Stage 3 built is
exactly what Stage 4 needs to expose: `make_rule_strategy` + `run_backtest`
is the backtester tool's actual implementation; the two-tier registry is
what makes an indicator tool meaningful to call at all.

Stage 5 is where the stakes change completely. Every discipline this stage
practiced in isolation — verify by execution rather than trust a claim,
disclose an exception rather than hide or accumulate it, prove a test isn't
vacuous by checking it fails the right way — becomes load-bearing under a
live agentic loop that can generate its own hypotheses and, left
unsupervised, would rather confirm a mediocre result than kill it. If any
part of Stage 3 were subtly wrong — a dead indicator parameter that slipped
through verification, a dedup bug that silently double-counts a signal, a
provenance field that's quietly stale — Stage 5's agent would inherit that
wrongness as ground truth and reason confidently from it. The clearest
symptom, if that happened, would likely surface exactly the way this stage's
own bugs did: not as a crash, but as a plausible-looking wrong number nobody
independently re-derived to check.
