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
