# Stage 5 — commit log

---

## Stage 5 component 1: dependencies, schema, tracing scaffolding

**Change:** Added 8 new deps (langgraph, sentence-transformers, pypdf,
langchain-text-splitters, arxiv, pgvector, tavily-python, langsmith);
`src/agentic_core/db/models.py` with 9 new tables on the shared `Base`
(charters, hypotheses, study_designs, study_runs, tool_call_traces,
verdicts, scoreboard_entries, corpus_papers, corpus_chunks); an Alembic
migration applied to both databases; one `@traceable` decorator on
`llm_client.structured_output`.

**Non-obvious:** `brew install pgvector` silently installs files only for
Postgres 17/18, not this project's Postgres 16 — built from source instead
(now documented in `CLAUDE.md`'s fresh-setup section, since the documented
flow would otherwise fail on `corpus_chunks`' vector column with no earlier
warning). `study_runs` was added mid-component after confirming it's free
for Stage 5's own logic and pre-empts a real multiple-comparisons
undercounting bug once re-testing exists later. Adding LangSmith env vars
broke `Settings()` (`extra_forbidden`) — fixed by declaring the fields, not
by loosening the strict-validation default. Full trail:
`docs/explanations/stage-5/step-01-dependencies-schema-tracing.md`.

---

## Stage 5 component 2: the charter

**Change:** `agentic_core/schemas.py` (`ParsedCharter`/`Charter` split so a
hallucinated ticker has no field to land in — `resolved_universe` doesn't
exist on anything the LLM produces) and `agentic_core/charter.py`
(`parse_charter`, `resolve_universe`, `create_charter`, `confirm_charter`);
`scripts/set_charter.py`, the interactive CLI standing in for Stage 7's
confirmation UI.

**Non-obvious:** real adversarial testing (four live Bedrock calls, not
mocked) found a genuine gap: grounding sector and industry as two
independent flat lists let the LLM combine two individually-real values
into a pairing that matches zero tickers (`sector='Consumer Cyclical'` +
`industry='Consumer Electronics'` — both real, never co-occurring). The
zero-match block caught it correctly; the prompt was then fixed to ground
on real `(sector, industry)` pairs instead, and the identical mandate
re-run to confirm the fix. Also established a reusable principle for
Component 6: retry design depends on who's watching — human-mediated retry
(re-run the script) is correct here because she's present; Component 6's
unattended loop will need automated retry-with-feedback instead, because
nothing else will be. `TAVILY_API_KEY` hit the identical `Settings()` gap
LangSmith did in Component 1 — same fix, now a recognized pattern. Full
trail: `docs/explanations/stage-5/step-02-charter.md`.

---

## Stage 5 component 2 follow-up: confirm-path verification

**Change:** no code changed. The step explainer had flagged confirmation's
`y` branch as untested — only the block path had been proven for real.
Ran `scripts/set_charter.py` end-to-end with the fixed "consumer tech
companies" mandate and a real `y` answer, then queried the row directly
from `strategy_research` rather than trusting the script's own printed
claim: `confirmed=t`, `confirmed_at` a few milliseconds after
`created_at`, the persisted `charter` JSONB matching the terminal output.
Step explainer's verification and honest-weaknesses sections updated —
the gap they named is now closed, not just noted.

---

## Stage 5 component 3 (part 1): Tier-1 corpus ingestion and retrieval

**Change:** `corpus_papers` schema fix (`id` slug replaces `arxiv_id` as
PK, since only 1 of 15 real papers is arXiv-native; new `fetch_path`
column) via migration `401a5c77cf08`. `CorpusEffectFamily` added to
`agentic_core/schemas.py` for the corpus's "methodology" tag without
widening the charter-facing `EffectFamily` enum. New
`agentic_core/corpus.py`: fetch dispatch on 4 `fetch_path` states
(arxiv/manual/manual_needs_confirmation/citation_only), pypdf extraction,
tiktoken-based chunking, asymmetric bge-small-en-v1.5 embedding,
pgvector cosine-distance retrieval. `scripts/ingest_corpus.py` prints a
full accounting of every entry, not just successes. Downloaded 5 real
manual PDFs (4 official NBER, 1 HEC Paris) plus 1 real arXiv auto-fetch;
6 papers, 612 chunks now genuinely in the retrievable corpus.

**Non-obvious:** two real bugs, both reproduced in minimal isolation
before and after their fix. (1) Autogenerate's corpus_papers migration
would have failed outright — it dropped the old PK column and added a
new one with no PK constraint, then tried to FK against it; fixed by
reordering and adding an explicit `create_primary_key`. (2) SQLAlchemy's
unit-of-work didn't sequence a `CorpusPaper` insert before its
`CorpusChunk` inserts despite correct `add()` order and no
`relationship()` linking them — a real gap in the ORM's documented
default behavior, not a batching quirk; fixed with an explicit
`session.flush()` at the boundary. Also fixed: literal NUL bytes in one
PDF's extracted text (Postgres can't store `\x00` at all) — same
disclosed-limitation category as "math/tables extract noisily." Three
real retrieval queries each landed exclusively on the correct paper out
of 6 candidates spanning 4 topics — real evidence of semantic retrieval,
not keyword luck. Open item, not yet resolved: `paper_list.json` has 15
entries, not the 14 originally stated — a real third mean-reversion
paper (`da_liu_schaumburg_reversal`, `year: null`) accounts for the
difference; whether that's intentional is the user's call, still open.
Full trail: `docs/explanations/stage-5/step-03-tier1-corpus.md`.

---

## Stage 5 component 3 (part 2): Tier-2 whitelist search and mechanical escalation

**Change:** `agentic_core/grounding.py` — `retrieve_whitelist` (Tavily,
domain-restricted to ssrn.com/papers.ssrn.com/nber.org/arxiv.org/
federalreserve.gov) and `ground_topic`, the mechanical escalation across
all three tiers, no LLM judgment anywhere in it. `GroundingChunk`/
`GroundingResult` added to `schemas.py`. New
`tests/agentic_core/test_grounding.py` — first formal regression test in
the new `tests/agentic_core/` package.

**Non-obvious:** a real adversarial test (not a confirming one) found a
genuine false positive — "January effect seasonality..." scored 0.782 via
`retrieve_local` against a Fama & French *value* chunk that genuinely
discusses the January effect as a caveat, wrong paper as primary grounding
despite real chunk-level topical overlap. 0.782 sits inside the 0.77-0.85
range of three previously-confirmed-correct matches, so no threshold
between those numbers cleanly separates true from false positive.
`LOCAL_RELEVANCE_THRESHOLD` raised 0.5 -> 0.90, deliberately conservative
given the asymmetric costs (wrong-paper citation vs. one extra cheap
Tavily call) — documented as PROVISIONAL directly in code, with the exact
calibration gap and revisit trigger named in the comment itself, not left
to memory. `WHITELIST_RELEVANCE_THRESHOLD` deliberately left unchanged —
no adversarial evidence exists against it yet, and raising it in sympathy
would be the same unfounded-tightening mistake in the other direction.
Also: a claimed NY Fed precedent for the Tier-2 domain list turned out not
to exist in the real, committed `paper_list.json` or its git history —
checked directly rather than assumed, kept the domain list at
federalreserve.gov only, as designed. Full trail:
`docs/explanations/stage-5/step-04-tier2-whitelist-and-escalation.md`.

---

## Stage 5 component 4: hypothesis generation

**Change:** `agentic_core/hypothesis.py` — `propose_hypothesis(charter_id,
family)`: grounds a fixed per-family query (Component 3), calls
`structured_output` for a `ParsedHypothesis` (rule/prediction/
falsification_condition/rationale), assembles `citations`/`grounding_tier`
from code, checks two guardrails (charter confirmed, family actually
requested), dedups by exact rule hash, persists. New schemas in
`schemas.py`: `FalsificationCondition` (single-clause, metric vocabulary
drawn from `BacktestResult`/`SignificanceResult`'s real field names),
`ParsedHypothesis`/`Hypothesis` (same LLM-produces/code-wraps split as
Charter).

**Non-obvious:** `StrategyRule`'s own Stage 3 validators (real indicator,
valid params, well-formed exit) already satisfy architecture.md's "confirm
the rule is executable" requirement, just by nesting `StrategyRule` inside
`ParsedHypothesis` — no new executability check was needed. Exact-hash
dedup (not fuzzy similarity) was a direct, deliberate response to
Component 3's threshold lesson — a second uncalibrated similarity
threshold here would risk the same mistake with even less data to check
it against. Real end-to-end run against a real confirmed charter produced
`grounding_tier='whitelist_search'` (not local) for a low-vol query —
Component 3's conservative `0.90` threshold pushing a real query to Tier
2, exactly as designed; Tavily found the correct paper anyway, including
the exact NBER working paper already sitting in the local corpus. Dedup
proven in both directions (a real repeat caught, a genuinely different
rule not flagged) via direct hash verification, since natural LLM
non-determinism made forcing a real collision impractical. Full trail:
`docs/explanations/stage-5/step-05-hypothesis-generation.md`.

---

## Stage 5 component 5: study design

**Change:** `agentic_core/study_design.py` — `propose_study_design(hypothesis_id)`:
computes the real cross-ticker common trading window from cached price
data (`_common_price_bounds`, the intersection of every universe ticker's
range, not the union), asks the LLM for only the genuinely fuzzy call
(`ParsedStudyDesign.design_type`/`split`), then computes actual calendar
windows in code (`_simple_holdout` / `_walk_forward_windows`) and persists
into the `study_designs` table that's sat unused since Component 1. New
schemas: `DateRange`, `ParsedStudyDesign`, `StudyDesign` (same
`ParsedX`/`X` split as Charter/Hypothesis). New `hypothesis.py` helper,
`hypothesis_from_row`, shared by this component and (later) Component 6.
14 new tests in `tests/agentic_core/test_study_design.py`.

**Non-obvious:** the intersection-vs-union choice for cross-ticker bounds
is the load-bearing decision — the union would let a newer ticker
silently get a shorter effective study than its peers, confounding
"different ticker" with "different calendar period" in exactly the
cross-sectional case architecture.md Step 3 describes; caught before it
ever ran for real, same standard as Stage 2's own lookahead gate. The
mandatory significance control has no field anywhere (no
`control_required` flag, not even one hardcoded `True`) — deliberately,
so there's no stored value an agreeable LLM or a careless future change
could flip; Component 6's loop enforces it as an invariant instead.
`split`'s fraction means "in-sample share of the whole span" identically
in both design types — walk_forward carves that same first share off,
then only chops the *remainder* into folds, rather than reinventing
per-fold splitting (which would require a re-optimization step this
system's fixed StrategyRules don't have). `null_hypothesis` is a fixed
constant, not LLM-written or per-design, since the control it describes
never varies either. `regime_split` (splitting by `classify_regime`
labels instead of calendar dates) is a named, deliberate gap, not built —
none of Component 4's six effect families obviously demands it yet. Two
real Bedrock calls (not mocked) verified both branches: the real
Component 4 low-vol AAPL hypothesis correctly got `simple_holdout`/`80-20`
with sound EMA(200)-warm-up reasoning in the LLM's own rationale; a
hand-built decay-framed hypothesis correctly got `walk_forward` with 5
real, gap-free folds. Dev database confirmed clean afterward by direct
query, not assumed. Full trail:
`docs/explanations/stage-5/step-06-study-design.md`.

---

## Stage 5 component 6a: execution loop — state, gating, and graph

**Change:** `agentic_core/loop_state.py` (LoopState with an append-only
`operator.add` reducer on `results`; EVIDENCE/DIAGNOSTIC tool tiers;
`available_tools`/`can_advance`/`can_conclude`; `build_decision_model`,
which rebuilds the LLM's response schema every step) and
`agentic_core/loop_graph.py` (five nodes, budget routing, `build_graph`
taking the MCP session and LLM as injected arguments). `window_index`
added to `ToolCallTrace` via migration `ac225385b472`;
`rule_indicator_names` helper added to Stage 3's `rule_strategy.py`; new
`loop_db_session` fixture. 41 tests across
`tests/agentic_core/test_loop_state.py` (31) and `test_loop_graph.py`
(10); suite 235 -> 276.

**Non-obvious:** every guarantee is enforced by SCHEMA OMISSION, not
validation — a forbidden tool has no name in the `Literal` enum, and
Advance/Conclude are absent from the union when their guard fails, so
there is no JSON the model could emit that means "conclude now." Same
structural-impossibility pattern as Component 5's missing
`control_required` field, applied to enum membership. The lookahead
guarantee is specifically that `CallTool` has NO date field: execute_tool
injects the window from state, so there is no LLM-supplied date in
existence to ignore. `extra="forbid"` matters more than it looks — without
it an invented `start` field would be silently dropped, leaving the action
looking clean while the intent went unrecorded. `can_conclude` requires the
FINAL window's own evidence too, closing a real gap an earlier draft left:
without it the agent could advance into the last fold and conclude
immediately, leaving it reachable but untested. `_successful_tools_in_window`
filters `is_error=False` so "make the backtest fail" can't become a way
through the gate. First implementation built CallTool by mutating
`model_fields` post-hoc — verified working on Pydantic 2.13, but replaced
with `create_model` because that is not a public contract and this schema
carries all three guarantees. Budget counted on `decide_next_action` (the
LLM call is the cost) and exhaustion routes to `status='failed'`, never to
a verdict. No LangGraph checkpointer, deliberately: `study_runs` and
`tool_call_traces` are already the durable record, and a second one could
disagree after a partial failure — the disclosed cost is that a crashed run
isn't resumable mid-step. Alembic's autogenerated migration was edited
before applying (added `server_default='0'`) — the NOT NULL column it
produced works only because the table is currently empty. VERIFICATION:
all 41 tests were mutation-checked — seven guarantees each deliberately
broken, every one caught by the specific tests that should catch it, both
files afterwards confirmed byte-identical to their pre-mutation backups.
The headline test drives the graph with a `LazyAgent` that always takes
the earliest exit available; it still runs the backtest AND the control in
every window including the last, because the alternatives were never in
its schema. Also retracted mid-component: a concern that LangGraph's
default `recursion_limit` would trip before MAX_STEPS was wrong — 1.2.11
defaults to 10007, not 25, verified by running a full 82-node-visit study
with no override. Full trail:
`docs/explanations/stage-5/step-07-execution-loop-state.md`.

---

## Stage 5 component 6b: the live execution loop + bounded retry

**Change:** `scripts/run_study.py` — the live runner (real MCP subprocess
over stdio, real Bedrock, step accounting, raw-output capture on
rejection). `loop_graph.py` gains bounded retry-with-feedback in
`decide_next_action` (`MAX_DECISION_ATTEMPTS=3`), a `decision_failed`
node, `_retry_prompt`, and `_compact`. `loop_state.py` gains `Rejection`,
the append-only `rejections` state field, `offered_actions`,
`classify_rejection`, and a stringified-decision validator.
`llm_client`'s docstring corrected. Suite 276 -> 287.

**Non-obvious:** the graph itself did NOT change to go live — session and
LLM were injected parameters from the start, so 6b swaps arguments, not
machinery. That was the whole point of the 6a/6b split and it held.
RETRY LANDED IN THE LOOP, NOT `llm_client`, reversing what that module's
own docstring promised since Stage 3: three callers (charter, hypothesis,
study_design) deliberately raise instead of retrying, each documented, and
a general retry underneath would override all three silently — plus a
caller cannot implement a BUDGETED retry on top of a function that already
retried invisibly. Docstring rewritten to record the reversal rather than
left reading as unfulfilled. Retry is safe on guarantee violations because
the response model is built ONCE before the loop and reused unchanged —
a locked tool is still absent from the enum on attempt 3, so a retry is a
second chance to pick something legal, never a second chance at the
forbidden thing (asserted by a test comparing two consecutive schema
builds). Feedback can safely name exactly why an action was refused: the
gate reads real trace rows, not claims, so there is no perfunctory path to
satisfy it. EVERY ATTEMPT COSTS A STEP — the budget is a cost control whose
unit is "LLM calls made"; free retries would let a malformed model generate
unbounded billable calls while step_count froze. Exhausted attempts route
to a recorded `status='failed'`, never to a verdict. If this starts
exhausting, the committed fix is restructuring the schema to remove the
nested `decision` object, NOT raising the attempt count. TWO REAL BUGS
found only by going live: (1) the stringified-decision failure — Claude
emits the nested decision as a JSON STRING; 2 of 3 runs died on it, and the
malformed variant carried a trailing brace so `json.loads` failed, which is
why the validator alone didn't save run 2. Run 3 succeeding was luck, not
proof, and was explicitly not treated as a fix. (2) prompt-size: every
diagnostic tool returns one record per trading day — 3,331 rows, ~320KB per
call, compounding each step — caught before any real spend; `_compact`
shrinks the PROMPT only, with a test asserting the stored trace stays
complete, since Component 7 validates against the table not the prompt.
MAX_STEPS CALIBRATION: 6 steps/2 windows and 12 steps/4 windows — exactly
3.0 per window both times, 15% and 30% of the 40 budget. LEFT AT 40
deliberately: both runs used zero diagnostic tools, so the branching path
architecture.md calls "the agency" is still unobserved, and it is exactly
the path that would consume extra steps. Honest scope: the live runs prove
the LOOP COMPLETES RELIABLY; they do NOT prove the RETRY RECOVERS, because
the retry path never fired — recovery is proven by unit tests replaying the
exact malformed string. Also: the no-checkpointer cost from 6a arrived
immediately, twice, as unresumable `status='running'` rows needing manual
cleanup. And a self-inflicted error worth recording: `git checkout` on
loop_state.py during mutation testing discarded that file's uncommitted
work (5 additions); all rewritten and re-verified byte-identical. Full
trail: `docs/explanations/stage-5/step-08-live-execution-loop.md`.

---

## Stage 5 component 7: the verdict (SACRED GATE 2)

**Change:** `agentic_core/verdict.py` -- three deterministic gates
(`decide_status`), the multiple-comparisons correction
(`corrected_threshold`), claim validation (`validate_claims`), the
orphan-number scan (`scan_for_unreferenced_numbers`), code-generated
`mandatory_caveats`, and `render_verdict` with bounded retry. New schemas
`Claim`/`ParsedVerdict`/`Verdict`. New columns `verdicts.caveats` and
`study_runs.failure_reason` (both deferred out of Component 6, decided
here) via migration `aaa61daf9d89`. `scripts/render_verdict.py`. 25 tests;
suite 287 -> 312.

**Non-obvious:** THE SCOPE DECISION IS THE LOAD-BEARING ONE. "Mechanical"
does not mean "honest" -- a human still picks WHICH DATA the mechanical
rule reads. On the real walk-forward study (win0 in-sample +0.771; OOS
-1.510 / +0.545 / +0.941), the same pre-registered bar gives opposite
falsification results depending on whether every OOS window or only the
last is scored. CORRECTION TO A PRIOR PREDICTION: narrowing Gate 1 does
NOT flip the real study to confirmed -- the falsification gate flips
PASS/FAIL, but the mandatory control independently rejects window 3
(p=0.312 vs 0.025). That is real defence in depth, and it meant the real
data could not by itself prove scope decides a verdict, so a SECOND,
clearly-labelled CONSTRUCTED test was added where every window beats the
control and one middle fold breaches the bar: all-windows=rejected,
final-window=confirmed. Sample adequacy is checked AFTER the failure gates,
deliberately: thin evidence downgrades a would-be confirmation to
inconclusive but can never rescue a failure -- which is what makes the
unanchored `MIN_TRADES_FOR_CONFIRMATION=30` safe to ship, since its only
possible error direction is toward caution. The grounding prior is
expressed as an ASSUMED EFFECTIVE SEARCH BURDEN (1.0/2.0/10.0), not an
alpha multiplier, because "effective tests" states a claim you can argue
with while a multiplier is a fudge factor; Harvey/Liu/Zhu is used as a
SANITY CHECK that 10.0 is not absurd, explicitly NOT as a derivation --
adopting their t>3.0 directly was rejected because the corpus deliberately
carries Chen & Zimmermann (2022) as the opposing view, and citing one side
of a disagreement the corpus was built to represent would dress an
arbitrary choice in a citation. Stage 4's BH `correct_p_values` is
deliberately NOT used: BH needs the full p-value set ranked together and
verdicts render sequentially, so Bonferroni-on-count-so-far now with raw
values preserved for a later cross-charter BH re-evaluation (which can
DEMOTE a confirmation -- that belongs to the scoreboard). LIVE BUG worth
recording: the first real run failed validation 3x with `0.5` and `30`
flagged as unreferenced numbers -- both legitimate system-supplied
constants missing from the allowlist. A too-tight check rejecting honest
prose, NOT fabrication -- and only diagnosable because
`VerdictValidationError` was improved mid-debugging to carry `errors` and
`narrative`. An error that cannot separate "the model lied" from "my check
has a gap" is not a diagnostic, least of all in the component whose whole
job is telling those apart. REAL RESULT: status=rejected, narrative opens
"The hypothesis is dead", 10 claims each independently re-verified against
its trace in a separate query, 5 caveats (3 mandatory + 2 model-added).
All four mutations caught (scope narrowing by 3 tests). HONEST LIMIT,
stated loudly: only the REJECTION path is proven -- the confirmation path
has never run on real data, and an agent that rejected everything would
pass every test in this file. Stage 6's golden set with planted-TRUE
hypotheses is what closes that. Full trail:
`docs/explanations/stage-5/step-09-verdict.md`.

---

## Stage 5 gating decision: the stage is not closed, and why

**No code change.** A correction to `step-09-verdict.md` and a decision
recorded here for the permanent record, prompted by a direct question
about why no `stage-5-summary.md` exists yet.

**The precedent, checked against the actual repo history, not assumed:**
Stage 3 and Stage 4 both wrote their Level-3 summary only after a
dedicated, self-contained gate-verification component -- `verify_stage3_
gate.py` (Stage 3 component 9) and the manual MCP verification script
(Stage 4 component 9) -- had run and passed. In both cases the summary
commit came strictly after the gate-script commit, with no interim
"pending" marker file; the absence of the summary file was itself the
signal that the stage wasn't closed yet. Stage 5 is following the
identical pattern: components 1-7 are done, but none of them is that
dedicated gate script, so `stage-5-summary.md` does not exist yet and
should not.

**A real mistake caught and corrected, not just a gap filled:**
`step-09-verdict.md` originally said Stage 6's golden set is what closes
Component 7's unproven confirmation path, and called it "the next thing I
would build." That is circular and wrong: the build order is explicit
that no stage begins before the previous one's gate has passed, so Stage
6 cannot be the mechanism that closes Stage 5's own gate without Stage 5
depending on a stage that, by the project's own rule, cannot yet exist.
Architecture.md's own description of the golden set confirms it isn't
built as a one-time gate-closer either -- it's meant to run continuously
in production for ongoing drift detection, a different job from proving
Sacred Gate 2 the first time. Three sentences in the step explainer
overstating this were corrected in place, marked as corrections rather
than silently rewritten.

**What actually closes Stage 5:** Component 8, `verify_stage5_gate.py` --
a dedicated gate script in the same style as Stages 2-4's own, using only
Stage 5's own tooling. It has two jobs Component 7 could not do alone:
prove the confirm path (never run on real data; unit-tested only against
constructed evidence) and adversarially attempt fabrication against the
live system (Component 7's fabrication tests are all synthetic claims fed
directly to `validate_claims`, not "make the real agent try to lie and
catch it"). The confirm-path proof will use a DELIBERATELY CONSTRUCTED
synthetic case with an unambiguous, built-in edge -- explicitly not a real
selected hypothesis, and explicitly the same reasoning Stage 2's own
lookahead gate used a deliberately cheating strategy rather than hoping a
real one would happen to reveal the bug. Selecting a real hypothesis
after the fact because it happens to confirm would be exactly the kind of
favorable-selection bias `.claude/rules/data-pipeline.md` and
`.claude/rules/backtesting-rigor.md` exist to catch elsewhere in this
project; there is no reason the gate script gets an exemption from that
discipline.

Only once Component 8 passes does `stage-5-summary.md` get written and
Stage 6 begin -- as the next stage built on a closed Stage 5, not as the
thing that closes it.

---

## Stage 5 component 8: verify_stage5_gate.py (SACRED GATE 2 PASSES)

**Change:** `scripts/verify_stage5_gate.py` -- Stage 5's own, self-contained
gate script, mirroring Stages 2-4's own pattern. Job 1 proves the CONFIRM
path (never run on real data before this) by driving a deliberately rigged
synthetic fixture through the real execution loop (real MCP subprocess,
real Bedrock deciding every action) and the real `render_verdict` (real
Bedrock writing the narrative). Job 2 proves fabrication is caught when it
reaches the live system, not just when handed to `validate_claims()`
directly, by corrupting a REAL Bedrock verdict response after the fact and
confirming it's rejected. Ran live: 6/6 checks passed. Full trail:
`docs/explanations/stage-5/step-10-gate-script.md`.

**Non-obvious:** THE FIXTURE TOOK THREE TRIES, and the first two failures
are real findings, not dead ends. v1 (a perfectly periodic 100<->130
staircase) scored 100% win rate, Sharpe 0.24 -- and p=1.0, complete control
failure, because a fully deterministic series gives RANDOMIZED entries an
equal shot at every jump; there's no informational edge to timing when
every transition is identical. v2 (dip immediately followed by rally) went
NEGATIVE (Sharpe -1.8, -1.27) -- `backtesting.py` fills orders at the NEXT
bar's OPEN, not the signal bar's close (Stage 2's own no-lookahead
discipline, catching a fixture bug its own author hadn't accounted for);
with no settle bar, the entry filled AFTER the rally, buying near the top.
v3 (used) adds a one-bar hold after every engineered move so any queued
order fills at a stable price regardless of next-open timing: Sharpe 0.932,
61 trades, 100% win rate, p=0.001 (the n=999 resample FLOOR -- 0/999 null
samples beat it) on BOTH independently-seeded windows. n_resamples was
raised from 300 to 999 mid-probe specifically because the 300-floor
(0.00332) sat uncomfortably close to the strictest grounding tier's 0.005
threshold in absolute terms, even though it was already floored -- free to
fix (local compute, zero API cost), so no reason to live with the
coincidence. grounding_tier is deliberately "none" (the harshest
multiple-comparisons correction) so a pass proves confirm survives the
worst case, not the easiest. The charter/hypothesis/design rows are
constructed directly in Python, NOT via Components 2/4/5's own LLM calls --
those translate fuzzy input into structure, which isn't what this gate
tests, and adding them would add non-determinism to a script whose whole
point is an unambiguous repeatable proof. A REAL TEST BUG was found and
fixed, and the diagnosis matters more than the fix: Job 2 first asserted
zero verdict rows after the corrupted attempt and got 1 -- the instinct to
suspect the code under test was wrong; the passing check right above it
(the exact corrupted value named against the exact real traced value) had
already proven detection worked, and render_verdict's own control flow
(raise happens before the write branch) proved the row couldn't be Job 2's.
It was Job 1's own legitimate confirmed verdict, reused deliberately to
avoid a second live loop run -- fixed by asserting the count is UNCHANGED,
not zero. A SEPARATE, UNRELATED MISTAKE: after the fix, the live script was
re-run a third time purely to reformat grep output -- real wasted API
spend, caught and disclosed rather than smoothed over; DB confirmed clean
by direct query regardless. A SECONDARY FINDING, logged not fixed:
`render_verdict` has no guard against being called twice for the same
study_run_id -- exposed only because Job 2 deliberately reused Job 1's run;
recorded as a Component 7 follow-up, since patching it isn't what this gate
requires. STAGE 5 IS NOW FORMALLY CLOSED -- `stage-5-summary.md` follows
this entry.
