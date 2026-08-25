# Stage 5 Summary — The Agentic Core

## 1. What this stage does

Every prior stage built a tool. Stage 5 is where the product this whole
project exists to be — an autonomous research agent — first came into
being. Before this stage, delete everything and there is still a complete,
independently useful backtesting library with a real MCP interface
(Stage 4's own closing claim). After this stage, delete it and there is no
product at all: no hypotheses, because nothing generates them; no studies,
because nothing designs or runs them; no verdicts, because nothing decides
them. That is `docs/architecture.md`'s own load-bearing test for what makes
this an agent rather than a tool, and Stage 5 is the stage that makes the
test's answer flip.

What exists now that did not before: a human mandate becomes a confirmed,
resolved research charter (Component 2); the agent grounds a hypothesis in
real literature across two tiers with mechanical escalation, or explicitly
flags when it can't (Components 3, 4); it designs a study whose shape —
simple holdout or walk-forward — depends on what the hypothesis actually
claims (Component 5); it executes that study through a LangGraph loop
whose every structural guarantee is enforced by *what the LLM's response
schema does not contain*, not by after-the-fact checking (Components 6a,
6b); and it reaches a verdict — confirmed, rejected, or inconclusive —
decided by deterministic code reading real recorded numbers, with the
model only ever explaining a decision already made, never making one
(Component 7). Component 8 is Stage 5's own gate script, and it is the
reason this document exists at all rather than being deferred indefinitely.

**What this stage is not.** It is not the frontend (Stage 7), not the
scoreboard or decay re-testing (`docs/architecture.md` Step 6), not
scheduled autonomous overnight runs (Step 8), and not the evaluation
harness that tests this stage's own claims at scale (Stage 6). Those are
real, separate, later work, and this stage does not simulate or
substitute for any of them.

---

## 2. The eight components, and where to read each one deeply

Synthesis only below — each component's full "why this and not the
alternative" treatment lives in its own step file:

1. Dependencies, schema, tracing scaffolding, and the `pgvector`-on-Postgres-16 build-from-source discovery — [step-01-dependencies-schema-tracing.md](step-01-dependencies-schema-tracing.md)
2. The charter — the one human-in-the-loop step, and the real sector/industry co-occurrence bug adversarial testing found — [step-02-charter.md](step-02-charter.md)
3. Tier-1 corpus ingestion and retrieval — [step-03-tier1-corpus.md](step-03-tier1-corpus.md)
4. Tier-2 whitelist search and mechanical escalation — including the real false-positive that set `LOCAL_RELEVANCE_THRESHOLD` — [step-04-tier2-whitelist-and-escalation.md](step-04-tier2-whitelist-and-escalation.md)
5. Hypothesis generation — [step-05-hypothesis-generation.md](step-05-hypothesis-generation.md)
6. Study design — the intersection-not-union bug class, caught before it ever ran for real — [step-06-study-design.md](step-06-study-design.md)
7. The execution loop's deterministic half — every guarantee proven by schema omission, mutation-tested against a hostile fake agent — [step-07-execution-loop-state.md](step-07-execution-loop-state.md)
8. The live execution loop and bounded retry-with-feedback — two real bugs found only by going live — [step-08-live-execution-loop.md](step-08-live-execution-loop.md)
9. The verdict — Sacred Gate 2's mechanism, and the scope trap that makes "mechanical" and "honest" two different claims — [step-09-verdict.md](step-09-verdict.md)
10. `verify_stage5_gate.py` — Stage 5's own gate, passed live — [step-10-gate-script.md](step-10-gate-script.md)

---

## 3. Cross-component decisions — the threads that run through the whole stage

**"Model proposes, code disposes" is not a slogan applied once — it is the
same structural move, made four separate times, at every single human/LLM
boundary this stage has.** Component 2 splits `ParsedCharter` from
`Charter`: the LLM proposes a sector, industry, and a *named* cut
(`quintile`/`tercile`/`decile`); code alone maps the name to a real
percentile (`CUT_TO_PERCENTILE`) and resolves it against real ticker data
— there is no field anywhere the LLM could use to name a raw threshold.
Component 5 does the identical thing one layer up: `ParsedStudyDesign`
lets the LLM choose `design_type` and a named `split`
(`"70/30"`/`"80/20"`), and code alone (`SPLIT_TO_FRACTION`,
`_common_price_bounds`) turns that into real calendar dates computed from
the actual cached trading calendar. Component 6a's `CallTool` schema is
the same idea pushed to its logical end: the model chooses a *tool name*
and a *ticker* from closed, dynamically-built enumerations, and the date
window and the frozen rule are injected by code from state — there is no
field for a date at all, so there is nothing to dispose of because there
was never anything to propose. Component 7 closes the loop: `ParsedVerdict`
has no `status` field whatsoever; the model is handed a status already
decided by `decide_status` and asked only to explain it. Four components,
four different kinds of fuzziness, one identical shape: the LLM names a
category from a closed vocabulary, and code alone turns categories into
numbers. None of these four were designed together in advance — each one,
once the pattern existed once, became the obvious next application of it.

**Every one of this stage's own real bugs was found by deliberately
attacking a design, never by trusting that it was probably fine.** This
is the same "verify by execution" throughline `stage-3-summary.md` and
`stage-4-summary.md` each named as running through their own stages,
continued here in a harder domain because the thing being verified is a
reasoning system rather than a deterministic function. Component 2's
sector/industry bug was found by four live adversarial Bedrock calls, not
by code review. Component 4's threshold was calibrated against a real
false positive a live query actually produced, not a guessed number.
Component 6a's three structural guarantees were each deliberately broken
in turn — availability tiers loosened, the final-window evidence check
removed, the control requirement dropped — and confirmed that the exact
tests meant to catch each one did. Component 7's scope decision was
proven load-bearing by literally computing what the real walk-forward
study's verdict becomes under a narrower scope, not by arguing it in
prose. Component 8's confirm-path fixture went through two real,
instructive failures — a perfectly deterministic series defeating the
control, and next-bar-open execution timing turning a "guaranteed"
dip-then-rally into a loss — before the third design actually worked. In
every one of these cases, the discipline was: attempt the failure, watch
what happens, believe the evidence over the prior expectation.

**Real live evidence was privileged over synthetic confidence at every
single load-bearing claim, and synthetic evidence was privileged for
everything else.** This split is deliberate and appears consistently:
Component 4's dedup logic, Component 6a's structural guarantees, and
Component 7's gate ordering are all proven by mutation-tested unit tests
against fakes, because those are properties about *code*, and code
behaves identically under a fake LLM and a real one. But the claims that
are actually about *the live system's behavior* — does the charter parser
handle a genuinely ambiguous mandate, does the loop survive a real
model's real serialization quirks, does the verdict mechanism reach
`confirmed` on real evidence, does a real corrupted response get caught —
were never trusted to a mock. Component 6b's entire existence is this
principle made concrete: Component 6a proved every guarantee holds
*structurally*, and 6b then proved the system still works when a real,
non-deterministic model is actually driving it — and found a bug 6a's
fakes could never have produced, because a fake constructs the response
object directly and cannot mis-serialize it.

**Deferred columns and deferred decisions were named explicitly at the
moment they were deferred, and each was picked up by name later rather
than rediscovered.** `window_index` on `ToolCallTrace` was flagged as a
real, needed schema change while Component 5 was still being written, and
landed with Component 6a, exactly when it was first needed.
`study_runs.failure_reason` and `verdicts.caveats` were named as open
questions in Component 6's own explainer and resolved with Component 7,
once that component's own design pass made clear what each needed to
hold. The circular Stage 6 dependency this stage's own step-09 document
briefly and wrongly implied was caught, corrected in place, and recorded
in the commit log with the reasoning intact rather than quietly edited
away — the record shows the mistake and the fix, not just the fix.

---

## 4. Concepts spanning multiple components

**Structural impossibility, as a security posture distinct from
validation.** Introduced properly in Component 6a and then recognizable
retroactively in Components 2, 5, and 7: a *validated* system accepts an
input and checks it against rules that live somewhere else, so the
guarantee is only as strong as the rule set's completeness and can rot
silently if a rule is forgotten. A *structurally impossible* action has no
representation at all — no field to name it in, no enum member to select
it from — so there is no rule to forget. `CallTool` having no date field,
`StudyDesign` having no `control_required` field, and `ParsedVerdict`
having no `status` field are the same idea at three different layers of
this stage, and Component 6a's own mutation testing is what proves the
distinction is real rather than merely aesthetic: breaking a validation
rule and breaking a structural omission require completely different
kinds of code change, and only the second kind is caught by "the field
doesn't exist" rather than "someone remembered to check."

**Mechanical does not mean honest.** The deepest lesson of this entire
stage, stated most sharply in Component 7: code reading real numbers with
a fixed rule *feels* objective, but a human still chooses which data the
rule reads. The real walk-forward study's falsification gate gives
opposite answers depending on whether it reads every out-of-sample window
or only the final one — no subjective judgment anywhere in either version,
and yet one answer is honest and the other is not. This is why Component
7's scope decision is defended by tests that assert on the *gate result*
against *real recorded numbers*, not merely argued in a docstring — and
why an author's own confident prediction about that decision (that
narrowing it would flip the real study) turned out to be wrong, corrected
by the same discipline of checking against real evidence rather than
trusting reasoning alone.

**The multiple-comparisons problem, worsened by self-generation.** An
agent that generates its own hypotheses can generate as many as it likes,
which makes it structurally capable of manufacturing a false positive
simply by continuing to search. Component 7's answer — an assumed
"effective search burden" per grounding tier, corrected sequentially via
Bonferroni with the raw values preserved for a later cross-charter
Benjamini-Hochberg pass — is this stage's concrete answer to a problem
`docs/architecture.md` names as one of the two biggest threats to the
whole project's honesty claim, the other being fabrication itself.

**A gate script as an established genre in this project, not a one-off
pattern.** Stages 2 through 5 now each close the same way: a dedicated,
self-contained script, run manually against real infrastructure, that
*deliberately attempts* the specific failure its stage's gate is worried
about rather than accumulating indirect confidence from unit tests written
to describe the system's own expected behavior. Component 8 is the fourth
instance of this genre, and it is the reason Stage 5's own closure has the
same evidentiary weight as Stage 2's lookahead proof, not a lesser,
"probably fine" version of it.

---

## 5. How Sacred Gate 2 was satisfied — exhaustively

Gate 2 has two halves, and they were satisfied by different components at
different times, which is itself worth stating plainly rather than
compressing into a single pass/fail moment.

**Never fabricates.** Component 7 built the mechanism: every quantitative
claim in a verdict carries a reference to the tool call that produced it;
`validate_claims` checks that reference against the real
`tool_call_traces` row, scoped to the correct study run, rejecting a
dangling reference, a mismatched value, a metric absent from the cited
trace, or a reference to another study's evidence entirely.
`scan_for_unreferenced_numbers` closes the remaining hole — a fabricated
number sitting in prose with no claim behind it. This was proven three
times, each a materially different kind of evidence: mutation testing
(deliberately breaking the validator and confirming the right tests
catch it), a real rejected verdict (ten claims, each independently
re-verified against its trace in a separate query, after the fact), and
Component 8's live adversarial injection (a genuinely real Bedrock
response, corrupted after the fact at the exact trust boundary, rejected
on every retry, with zero net change to the persisted verdict count).

**Kills hypotheses when the evidence says to.** Component 7 also proved
this, once, on real data: a real walk-forward study, deliberately ending
on a flattering final Sharpe of 0.94, correctly reached `status =
'rejected'` with a narrative opening "The hypothesis is dead" — no
softening, because the model was told the outcome and asked only to
explain it, never to reach it. But this half of the claim had a real,
acknowledged hole until Component 8: an agent that rejected *everything*
would have passed every test in Component 7's own suite, because nothing
before Component 8 had ever produced a `confirmed` status on real data.
Component 8 closed that hole with a deliberately engineered fixture whose
edge is real, statistically overwhelming, and known in advance by
construction rather than selected after the fact — Sharpe 0.93, p=0.001
(the resample floor), 61 trades, on two independently-seeded windows —
run through the real loop and the real verdict writer, correctly reaching
`confirmed`.

**What this gate does not prove, stated as loudly as the project's own
convention requires.** One real confirmation and one real rejection are
existence proofs, not reliability proofs. Neither establishes that
Component 7 draws the line correctly on a real, genuinely ambiguous
hypothesis — the harder and more realistic case, where the honest answer
is a close call rather than an engineered landslide in either direction.
Component 8's own fabrication test corrupts exactly one claim's numeric
value; it says nothing about a corrupted metric name, a corrupted trace
reference, or a qualitative claim with no number in it at all — all
covered only by Component 7's own synthetic unit tests, never against a
real live response. The double-verdict gap Component 8 exposed (nothing
stops `render_verdict` from being called twice against the same study
run) remains open, logged as a Component 7 follow-up rather than patched.
And `MAX_STEPS`, the retry-attempt cap, and every provisional constant in
`verdict.py` (the 30-trade floor, the three grounding-tier burden values)
are calibrated on a small number of real observations, disclosed as
provisional inside every verdict the system writes, with named revisit
triggers rather than false precision. Closing the reliability question —
not "can it," but "does it, reliably, across many real and ambiguous
cases" — is Stage 6's job, deliberately, and is why that stage exists as
real, separate work rather than as decoration on top of this one.

---

## 6. Interview defense — the stage as a whole

**"What makes this an agent, and not just a very elaborate function
call?"** The load-bearing test this project defines for itself: delete
the agent and see what's left. Delete Stage 5 and there is no product —
no hypotheses (the agent generates them, grounded in retrieved literature
it decides to search for), no study design (the agent decides whether a
plain holdout or a walk-forward re-test fits *this* hypothesis's claim),
no execution path (the agent decides, at every step, which tool to call
next based on what the last one returned), no verdict narrative. What's
deterministic — the backtester, the gates, the claim validator — is
deliberately *not* the agent; it's the check that keeps the agent honest.
The agency is entirely in the branching: nobody wrote the decision tree
that leads from "in-sample Sharpe 1.34" to "run the out-of-sample window"
to "investigate the regime because it dropped to 0.21" — the model
generates that path, one legal choice at a time, from a schema that only
ever offers it real, currently-available options.

**"Why didn't you just let the LLM write more of this directly, given how
capable modern models are?"** Because capability and trustworthiness are
different properties, and this project is built around a specific,
falsifiable claim: every quantitative decision is made by code reading
real numbers, and the LLM only ever explains decisions or translates
fuzzy human intent into closed-vocabulary structure. A more capable model
does not change the fact that "the model's own judgment decided this
number" is unverifiable in a way that "code compared 0.771 to 0.5" is
not. The entire multi-component structural-impossibility pattern —
`CallTool`'s missing date field, `StudyDesign`'s missing
`control_required` field, `ParsedVerdict`'s missing `status` field — exists
specifically to make that boundary un-crossable by construction, not by
asking a smarter model to behave better.

**Hard question: "You have exactly one real confirmed hypothesis and it's
synthetic. Isn't your whole 'kills bad hypotheses' story built on a
sample size of one?"** Yes, and I'd rather say that plainly than argue
around it. The rejection side has real, if still limited, weight: two
live studies on genuine market data, both correctly killed, one of them
specifically chosen because it ends on a number that would tempt a
softer system to hedge. The confirmation side has exactly one data point,
and it's engineered, not discovered — deliberately, because selecting a
real hypothesis *after* seeing that it confirms would be exactly the kind
of after-the-fact favorable selection this project's own rigor rules
forbid everywhere else. What that buys is a real, if narrow, existence
proof: the mechanism *can* reach `confirmed` correctly under unambiguous
evidence. It does not buy reliability under ambiguous evidence, and I
wouldn't claim it does. That gap has a name and an owner — Stage 6's
golden set, built specifically to run many cases, including deliberately
non-obvious ones, and to catch drift continuously rather than once.

**Honest weaknesses, stated the way the project's own convention
requires: plainly, not defensively.** The confirm path has one real
data point. The grounding-tier multipliers and the 30-trade sample floor
are reasoned but openly provisional, disclosed as such inside every
verdict. `render_verdict` can be called twice against the same study and
produce two rows; nothing prevents it yet. The loop's tool vocabulary is
deliberately narrower than `docs/architecture.md`'s own list — a
disclosed, argued deviation, not an oversight. And this stage's own
verification record includes a real, self-inflicted mistake: a live gate
script run a third time, unnecessarily, for no better reason than
reformatting terminal output, at real API cost. None of these are hidden
in this document or in any of the ten step files behind it; the
convention this entire stage was built under is that a named limitation
is stronger evidence of rigor than a clean-looking absence of one.

---

## 7. What comes next and why

**Stage 6 — the evaluation harness.** A golden set of hypotheses with
known correct verdicts: planted false ones the agent must kill, planted
true ones it must confirm (the property this stage has now proven
*possible* exactly once, which Stage 6 tests at real scale), and
known-caveat cases where the honest answer is "insufficient evidence."
Run on every future agent change and continuously in production, it
becomes this project's drift detector — when a planted-false hypothesis
starts passing, something has quietly broken, and that alert is what
closes the loop with `docs/architecture.md`'s Step 8 (scheduled
autonomous runs).

**`docs/architecture.md`'s Step 6 — the scoreboard** inherits two things
this stage deliberately built and did not spend: `window_index` on every
trace, ready for a future re-test to attribute claims correctly across
re-runs, and the raw p-values plus hypothesis count preserved in every
verdict, ready for a proper cross-charter Benjamini-Hochberg
re-evaluation that can demote an earlier confirmation as evidence
accumulates.

**If this stage were wrong, here is how it would surface, and why the
next stage is built to catch exactly that.** A broken structural
guarantee — a date field re-added to `CallTool`, a scope narrowed in
`decide_status`, a validation check loosened in `validate_claims` — would
not announce itself. It would produce verdicts that are internally
consistent, fully sourced, confidently stated, and quietly wrong, because
every individual number in them would still be real. Nothing in Stage 5
alone would flag that; the numbers would check out. That is precisely why
Stage 6 exists as genuinely separate, continuously-run work rather than
as a formality after this stage's own gate script: a single manual proof,
however honest and however carefully attempted, is still one point in
time, on cases this stage's own author chose. A system that must
correctly judge hypotheses it did not design the test cases for, run
again on every future change, is the only mechanism that actually
protects this stage's claims once nobody is watching the gate script run.
