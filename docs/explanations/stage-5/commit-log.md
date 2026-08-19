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
