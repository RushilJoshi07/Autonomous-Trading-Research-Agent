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
