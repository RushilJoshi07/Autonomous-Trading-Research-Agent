# Stage 6 Summary — The Evaluation Harness

## 1. What this stage does

Stage 5 closed with Sacred Gate 2 proven *possible*: one real rejected
walk-forward study, and one deliberately engineered confirmed study
(`GATE5PROBE`, Component 8's own fixture). `stage-5-summary.md` named the
resulting gap by its own hand on the way out: *"you have exactly one real
confirmed hypothesis and it's synthetic... that gap has a name and an
owner — Stage 6's golden set."* `docs/architecture.md` Section 9 specifies
what that golden set has to be: a fixed collection of hypotheses with
known correct verdicts — planted false, planted true, known-caveat — run
automatically, scored on the three dimensions the project itself names
(status correctness, fabrication cleanliness, caveat presence), and run
continuously in production as a drift detector.

Stage 6 delivers exactly that, and — after five genuinely eventful live
attempts, three of which found real bugs — proves it live: six
deterministic fixtures (two planted-true, three planted-false, one
known-caveat), each isolating a different one of `agentic_core.verdict.
decide_status`'s three independent gates; a harness that drives every one
of them through the real, Bedrock-driven execution loop and the real
`render_verdict`, scoring the result; a zero-cost test suite for
everything in that harness that doesn't require touching Bedrock; and a
gate script that deliberately breaks the agent and confirms the harness
notices. All five components exist and all five are proven, live, not
just numerically.

**What this stage is not.** It is not a claim that six engineered
fixtures answer the reliability question this project has held open
since Stage 5 — Section 5 below states exactly what remains open, and it
is real. It is not the scoreboard (`docs/architecture.md` Step 6), not
scheduled continuous production runs (Step 8), and not a general-purpose
adversarial test suite against every possible way `agentic_core` could
fail — only one deliberate sabotage was tested, chosen because
architecture.md names it as the central worry, not because the catalogue
is exhaustive.

Full component detail lives in four step files, referenced rather than
repeated below: [step-01-golden-cases.md](step-01-golden-cases.md) (the
six fixtures), [step-02-harness.md](step-02-harness.md) (the harness that
drives them live), [step-03-test-suite.md](step-03-test-suite.md) (the
zero-cost test suite), and
[step-04-gate-script.md](step-04-gate-script.md) (the gate script, and
the resumability system built in direct response to a real, recurring
live failure).

---

## 2. The five components, synthesized

**Component 1** built the fixtures with their verdicts declared *before*
anything that could produce a verdict ever touched them — the same
anti-hallucination discipline `.claude/rules/agent-honesty.md` requires
of the research agent itself, turned here onto the harness that tests it.
**Components 2–3** built the harness that actually runs those fixtures
live and the thin script that invokes it. **Component 4** built the
zero-cost regression suite for everything in that harness that doesn't
require a live Bedrock call. **Component 5** built the gate itself,
proving the whole thing catches a deliberately broken agent — and, along
the way, found and fixed two real bugs in already-shipped code from
Components 1 and 2, plus one bug in its own new code, none of which any
earlier verification layer had caught.

---

## 3. Cross-component threads

**"Verify by execution, never trust that it's probably fine" ran through
every layer of this stage, not once but repeatedly, each time catching
something the previous layer structurally could not have.** Component 1
verified its six fixtures' real Sharpe ratios, p-values, and trade counts
directly against `run_backtest`/`test_significance` before ever wiring
them into a case — and then, separately, ran a database-level smoke test
before trusting persistence at all, which is what caught the
ticker-length bug: the numeric spike used an in-memory `DataFrame` and
never touched Postgres, so it could not have found a `PriceBar.ticker`
column-width violation no matter how thoroughly it ran. Component 5
repeated the same discipline at a different bug entirely: when a live
assertion failed on the very first sabotaged case, the response was not
another live retry — it was a ten-line, zero-cost, fully isolated
reproduction using `types.SimpleNamespace` and `patch.object`, unrelated
to Bedrock or MCP in any way, that settled whether the bug was in
`unittest.mock.patch` itself or in the check's own placement *before* any
further money was spent. Both are the identical move Stage 5's own
`step-10-gate-script.md` already modeled for its own bug: read what the
evidence actually shows, and verify the diagnosis cheaply before trusting
it.

**Every component in this stage found at least one real bug, live, and
none of them were designed in.** Component 1's ticker-length violation.
Component 2's unguarded `case = builder()` call, which let a database
collision crash an entire batch before any cleanup ran. Component 5's
own restoration-check placement bug. And, most tellingly, a bug that
Component 2 shipped and *explicitly defended in its own step
explainer* — `run_case`'s narrow `except VerdictValidationError` around
`render_verdict`, reasoned at the time to be correct because "an outer
caller should catch anything else" — which a live rate limit during
Component 5's own gate runs proved wrong, because the exception got
wrapped in nested `ExceptionGroup`s by the MCP session's own async
machinery and escaped every `try/except` nested inside that scope,
including the ones Component 2 had trusted to catch it. That bug was
fixed by rewriting `run_case`'s own contract — "always returns a
`CaseResult`; never raises" — rather than patched around at the call
site, and this document is not the first place that fix is disclosed:
`step-02-harness.md` itself was revisited and its own claim corrected,
in the open, rather than left standing as a quietly-outdated description
of code that no longer behaves the way it says.

**The pure/impure split, the same shape at a third layer now.** Stage 5
split `decide_status` (pure, mutation-tested) from the loop's own nodes
(impure, live-only). Stage 6 applies the identical split one level up —
`eval.harness._score` (pure) versus `run_case`/`run_golden_set` (impure,
live-only) — and explicitly declined to build the DB-fixture machinery
that would let `render_verdict`'s own orchestration be unit-tested with
fakes, on the grounds that Stage 5 never built that machinery either, for
the same real reason (genuine, multi-table database I/O that this
project has consistently chosen to verify live rather than mock). Then
the split appears a *third* time, inside Component 5's own resumability
code: `eval.resumable.run_with_pacing`/`resume_action` are generic and
pure enough to unit-test with fakes in milliseconds; the gate script's
own `run_healthy_phase`/`run_sabotage_phase` are not, and are verified
only by the live runs `step-04-gate-script.md` documents in full. Three
independent applications of one idea, not a coincidence — once a project
has decided which properties are "about code" and which are "about the
live system," that division keeps reproducing itself correctly at every
new layer someone adds.

**Honesty about scope and cost as a running discipline, not a one-time
disclosure.** Component 1 named three separate things that didn't go as
first planned — a grounding-tier deviation discovered by computing a real
threshold, an unexplained trade-count anomaly deliberately left
untraced, and the ticker-length bug — each in the order it actually
happened, not smoothed into a clean narrative after the fact. Component 2
totaled its own real cost overrun honestly: roughly $0.75–0.85 against a
clean $0.34 estimate, entirely attributable to a named mistake (re-running
a crashed script without checking database state first), not to any
single run costing more than predicted. Component 5's cost story spans
five live attempts and is told the same way. And the "estimate versus
measurement" framing `step-08-live-execution-loop.md` introduced for
`MAX_STEPS` in Stage 5 reappears here for the *second* time in this
project's history — first applied to how many decision steps a study
needs, now applied to how many real dollars a golden-set run actually
costs, measured directly rather than assumed from a documented aggregate
figure that turned out to be measuring something proportionally
different (fuller studies with grounding-retrieval text these fixtures
never pay for).

**Deliberately constructed fixtures, never selected after the fact — the
agent's own anti-hallucination rule, turned onto the harness that
grades it.** Every golden case's expected verdict was fixed in code
before the fixture was ever run through anything that could produce a
verdict, mirroring `.claude/rules/agent-honesty.md`'s pre-registered
falsification requirement exactly. Component 1 went further and rejected
real cached market data as a golden-case source specifically because the
data pipeline's own *correct* behavior — corporate-action re-fetching —
could silently move a case's "known truth" out from under the harness
between runs, which would make the harness's own alarm unreliable for
precisely the reason it exists: to detect drift in the *agent*, not
accidental drift in the *fixture*.

---

## 4. Concepts spanning multiple components

**Structural containment versus asserted containment.** Component 5's
sabotage phase doesn't just *use* `unittest.mock.patch` correctly — it
checks, after every single sabotaged case, that the patch actually came
off, rather than trusting the language's own guarantee silently. That
checking-what-should-already-be-guaranteed instinct is the same one
Component 1 and Component 2 both applied to database cleanup
(`verify_cleanup`, queried directly, never assumed from a deletion call
having executed without error) — a pattern this project now applies at
every layer where "this should be true" is cheap to actually confirm
rather than merely expect.

**A rate limit as real evidence about infrastructure, not just an
obstacle.** Three independent live failures, each landing at roughly the
same point in an identical, unpaced sequence, is itself information about
where a resource ceiling sits — weaker evidence than reading AWS's own
quota (blocked here by this project's own correctly narrow IAM scoping),
but not nothing. Component 5 treated the *shape* of repeated failure as
data worth reasoning from, rather than as pure bad luck to route around
without asking why it kept happening at the same place.

**Resumability as a response to demonstrated failure, not speculative
design.** `eval.resumable`'s entire existence is a direct, traceable
consequence of one script hitting the same real external constraint
three times — not a general "make everything resumable" instinct applied
in advance. The module is generic enough that `run_golden_set` could
adopt the same pacing later with no new design work, which is a genuine
benefit, but it was not the reason the module got built; the reason was a
script that had already, empirically, stopped being a reliable one-shot
proof.

---

## 5. What the gate proved — exhaustively — and what it did not

**Proved.** All six real, live cases — not Stage 5's single synthetic
`GATE5PROBE` fixture — reach their declared verdict through the actual,
unmodified, Bedrock-driven system: two reach `confirmed` via genuinely
different engineered edges, three reach `rejected` through three
*different* mechanisms (no edge at all; a real profitable exit paired
with an uninformative entry, caught specifically by the anchored
randomized-entry control; an outright inverted rule breaching the
falsification bar directly), and one reaches `inconclusive` via sample
adequacy alone. That is broader, more varied evidence for the "kills bad
hypotheses" half of Sacred Gate 2 than Stage 5 ever produced, because
Stage 5's own two real rejections both happened to fail via different
paths but were never *designed* to isolate specific gates the way these
three were.

Separately, and for the first time anywhere in this project: the harness
was proven to catch a real, deliberately introduced regression.
`agentic_core.verdict.decide_status` was forced, via a contained,
verified-restored monkeypatch, to always report `"confirmed"` — and every
one of the four cases whose real evidence says otherwise correctly
stopped passing, independently, each confirmed by a fresh restoration
check. Nothing in Stage 5 ever tested this; Stage 5's own gate script
proved the mechanism works when it is *not* broken. Stage 6's gate proves
something categorically different: that a specific way of breaking it
gets *noticed*.

**Does not prove — reliability on a genuinely ambiguous real hypothesis.**
`stage-5-summary.md`'s own "one real confirmed hypothesis, and it's
synthetic" critique is answered with more data — two confirm shapes
instead of one, three isolated reject mechanisms instead of relying on
whatever a real market happened to produce — but it is not eliminated as
a category of limitation. All six cases remain Component 1's own
engineered fixtures, built with the answer known in advance. The harder,
more realistic question — does this system draw the line correctly on a
real hypothesis where the honest answer is a close call, not an
engineered landslide in either direction — is still open, exactly as
`stage-5-summary.md` predicted it would be when it named this stage as
the place that question belongs.

**Does not prove — robustness against any other kind of deliberate
breakage.** Only "always confirms" was tested. A loosened
`mandatory_control` threshold, a flipped comparison direction inside
`decide_status`, or a quietly disabled check inside `validate_claims`
are real, plausible ways this system could fail, and none of them are
exercised by this gate's own proof. The choice of sabotage was deliberate
— `docs/architecture.md` names an agreeable, always-confirming model as
the central worry — but deliberate is not the same as exhaustive, and
this document says so rather than letting one passing gate imply broader
coverage than it has.

**Does not prove — that `is_rate_limited`'s heuristic will keep working.**
It is a plain string match against an error message, disclosed as such
in its own docstring. A differently-worded throttle error, or the same
condition raised under a different exception class by a future SDK
version, would not trip it. The residual risk is real and named directly
in `step-04-gate-script.md`: the circuit breaker would stop functioning,
though `run_case`'s own now-guaranteed "never raises" contract means the
degraded behavior is a return to Attempt 1–2's crash-free-but-unpaced
state, not a regression to something worse than this stage started with.

---

## 6. Interview defense

**"What does Stage 6 add that Stage 5's own gate script didn't already
prove?"** Stage 5 proved the mechanism *can* reach a correct confirm and
a correct reject — once each, on cases the project's own author selected
and built. Stage 6 proves it does so across a small but deliberately
varied battery — two different confirm shapes, three genuinely different
reject mechanisms, one caveat case — and, separately, proves that the
*harness itself*, the thing whose entire purpose is catching future
regressions, actually catches a regression when one is deliberately
introduced. Nothing in Stage 5 ever tested that second claim, because
Stage 5's own gate script was never run against a broken agent — only
against the real one.

**"Why didn't you just add more golden cases instead of building an
entire resumability system for a gate script that runs once?"** Because
the resumability system was not optional scope expansion — it was the
only way to actually *finish* running the gate script live, after the
same real AWS Bedrock rate limit interrupted three consecutive attempts.
More golden cases would have made that specific problem strictly worse,
not better: every additional case is more real Bedrock calls packed into
the same unpaced burst, closer to the same ceiling, not further from it.
The fix had to address the actual constraint that was failing, not add
more work for that constraint to fail on.

**Hard question: "You found five real bugs across five components in one
stage. Doesn't that suggest the development process here is unreliable?"**
I'd argue close to the opposite, and I want to walk through why rather
than just assert it. Every one of those five bugs was found *before* it
could cause silent harm — by a database smoke test, by a live gate run,
by a zero-cost isolated reproduction — specifically because this
project's standing discipline is to run things for real and check the
result, not to assume that code which compiles and passes a mocked test
does what it claims. Two of the five were in already-shipped code whose
original design had explicitly, reasonably defended the choice that later
turned out to be incomplete — and both were corrected in the open, with
the earlier document's own claim revised rather than left standing. A
stage this eventful — doing more genuinely new live orchestration against
real external infrastructure than any prior stage, with real rate limits
and real async failure modes neither Stage 2 through 5 ever had to
contend with — finding zero bugs would be the far more suspicious
outcome, because it would mean either the work was less thorough or the
checking was.

**Honest weaknesses, stated plainly rather than defensively.** The
fixtures are still engineered, not real. Only one sabotage was tested,
chosen for its centrality to this project's own stated worry, not because
it exhausts the space of plausible regressions. The rate-limit circuit
breaker is a heuristic string match, disclosed as one, not a typed
signal. `PACE_SECONDS = 120` is a deliberately generous guess grounded in
the *pattern* of three real failures, not a number read from AWS's own
quota system — which remains genuinely unreachable, a direct consequence
of this project's own correctly narrow IAM permissions rather than an
oversight. None of these are hidden in this document or in the four step
files behind it. Naming them here is what this project's own convention,
followed every stage before this one, requires.

---

## 7. What comes next and why

**`docs/architecture.md`'s own build order** puts Stage 7 (the frontend —
research log, scoreboard, expandable traces) next, then Stage 8 (deploy
and monitor). Stage 6 hands Stage 8 something concrete and specific: the
golden set, the harness, and the gate script are the actual mechanism
Stage 8's own "run continuously in production" requirement
(`docs/architecture.md` Step 8, Step 9) will invoke on a schedule. This
stage built the thing that has to run; Stage 8 is what turns "run it
continuously" from a manual discipline — remembering to invoke `scripts/
run_golden_set.py` by hand — into an actual, standing guarantee. The
generic pacing and resumability primitives `eval.resumable` now provides
are also directly reusable there: a scheduled job that hits the same
kind of external rate limit Component 5 hit five times has the same
tested tools already available, at no additional design cost.

**If this stage were subtly wrong, here is how it would surface, and why
that is the honest answer rather than a reassuring one.** A bug in
`_score`'s own logic — say, `caveats_ok` silently returning `True` when
it shouldn't — would not show up anywhere in this stage's own live
6/6-and-27/27 results, because none of the six real cases happen to
exercise that exact edge in a way that would expose it. It would surface
only once something is watching for exactly the kind of silent scoring
error this stage cannot catch of its own accord — which is precisely
what Stage 8's continuous monitoring exists to be, and precisely why a
golden set that only ever runs once, by hand, when someone remembers to,
would be a weaker guarantee than this project's own stated ambition for
it. Stage 6 proved the mechanism works today, against the agent that
exists today. Whether it keeps working as the agent changes is a claim
only repeated, scheduled, unattended execution can make — and that claim
belongs to Stage 8, not to this one.
