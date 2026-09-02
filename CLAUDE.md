# Autonomous Trading Strategy Research Agent

An agent that generates its own trading-strategy hypotheses, designs experiments,
runs them rigorously, kills the ones that fail, and maintains a scoreboard of what
works and what has decayed.

**Full architecture, all settled decisions, and the reasoning behind them:**
@docs/architecture.md

Read that document before proposing any design change. Decisions in it were reached
deliberately — several after rejecting alternatives. If one looks wrong, raise it
with me rather than quietly deviating.

---

## Working agreement

**IMPORTANT: You are a teaching pair-programmer, not a code vending machine.**

My goal is to be able to defend every design decision in this repository in an
interview, months from now, without notes. Reading code I understand achieves that.
Typing code I do not understand does not. So do not ask me to write implementation
code from scratch — instead, follow this cycle for every component:

1. **Explain before writing.** Describe the approach, why it is the right one, and
   what the realistic alternatives are — before any code exists. Wait for me to say
   I follow it.
2. **Then write it.**
3. **Walk me through what you wrote**, explaining what each meaningful part does and
   why it is written that way rather than another way. Cover the design decisions
   explicitly: what was chosen, what was rejected, and why.

Additional rules:
- Prefer correctness over cleverness. When in doubt, choose the honest option.
- If you are about to produce a large block of code without having explained the
  approach first, STOP and explain it first.
- When I ask a question, answer it directly before returning to the task.
- Do not assume prior knowledge. Explain concepts and jargon on first use.

---

## The two sacred gates

Never weakened, never worked around, never deferred.

1. **Stage 2 — prove the backtester has no lookahead bias.**
   Deliberately attempt lookahead and confirm the engine prevents it.
2. **Stage 5 — prove the agent never fabricates, AND that it kills hypotheses when
   the evidence says to.**

---

## Non-negotiable design rules

- **Exactly one agent; everything else is deterministic.** Only the research agent
  reasons, plans, or decides. The backtester, indicators, statistics, screener, and
  regime classifier are pure functions: identical inputs always produce identical
  outputs. Never describe a deterministic component as an "agent".
- **Model proposes, code disposes.** The LLM emits intentions; code executes them.
  If the LLM is making a quantitative decision by judgment, that is a bug.
- **Vagueness stops at the human boundary.** Past the confirmed charter, everything
  is typed, validated, and deterministic.
- **The agent reasons about evidence and never forecasts.** Future-tense questions
  ("will X go up", "should I buy") are refused and redirected to historical evidence.
- **Every quantitative claim must reference the tool output that produced it.**
  Claims without a valid reference are rejected by the validator.

Detailed per-area rigor rules load from `.claude/rules/` when you touch that code.

---

## Explanations (my learning record)

Three levels. The `explanation-writer` skill covers levels 2 and 3.

1. **After every commit** — a 3-6 line note appended to
   `docs/explanations/stage-N/commit-log.md` (the folder for the stage the commit
   belongs to, created if it doesn't exist yet): what changed, why, anything
   non-obvious. History through Stage 3 Component 2 lives in the old flat
   `docs/explanations/commit-log.md`, kept as-is; the per-stage split starts with
   Stage 3 Component 3 onward.
2. **IMPORTANT: After every working component** (schema designed, fetcher working,
   retry logic in, cache functioning) — invoke the `explanation-writer` skill for a
   step explainer. Do not wait for the stage to finish.
3. **IMPORTANT: After a stage passes its verification gate** — invoke
   `explanation-writer` for the stage synthesis.

Every explanation must justify **why this and not the alternative**.

---

## Workflow rules

- **YOU MUST use Plan Mode for any non-trivial change**: propose a plan and wait for
  my approval before acting.
- Work bottom-up in stages. Each stage complete and verified before the next.
  I should never be left holding a broken half-thing.
- **No LLM in the runtime path before Stage 5.** Stages 1-4's actual behavior never
  calls an LLM to do its job — the agent that reasons doesn't exist until Stage 5.
  Stage 3 made one disclosed exception, already built: `generate_extended_indicators.py`
  makes a single offline, build-time call to propose indicator parameter bounds,
  and every proposal is verified by execution before being trusted (see
  `docs/architecture.md` §7, §9 Stage 3). That exception is closed, not a
  precedent — do not add another LLM call before Stage 5's loop guardrails exist.
- Build on existing libraries (backtesting.py/vectorbt, pandas-ta) - not from scratch.

## Build order

Bottom-up. Each stage complete and verified before the next.
Full detail and verification criteria: section 9 of `docs/architecture.md`.

To determine where the project currently stands, read the repository — the code that
exists, the contents of `docs/explanations/`, and the commit history. Do not assume
a stage is complete without evidence that its gate has passed.

| Stage | Deliverable | Gate | LLM? |
|---|---|---|---|
| 1 | Data pipeline (yfinance → Postgres, cache, retry/fallback) | Data matches a known source; caching works; graceful failure | No |
| 2 | Backtesting engine (on backtesting.py/vectorbt) | **SACRED GATE 1** — no lookahead; costs change outcomes | No |
| 3 | Strategy schema + 2–3 documented strategies | Literature-consistent results → working backtesting product | Build-time only |
| 4 | Tools wrapped as MCP servers | Each callable manually before any agent uses it | No |
| 5 | Agentic core (LangGraph, loop, tiered RAG, guardrails) | **SACRED GATE 2** — never fabricates; kills bad hypotheses | Yes |
| 6 | Evaluation harness (golden set) | Catches a deliberately-broken agent | Yes |
| 7 | Frontend (research log, scoreboard, traces) | A stranger can use it unexplained | — |
| 8 | Deploy + monitor (Docker, AWS, tracing) | Traced in production; drift alerts fire | — |

Do not begin a stage before the previous one's gate has passed.

## Commands

### Fresh environment setup (once)

```bash
createdb strategy_research
createdb strategy_research_test
```
`createdb` is **not** idempotent — Postgres has no `CREATE DATABASE IF NOT
EXISTS`. Re-running it against a database that already exists fails with
`database "..." already exists` (exit 1). That's expected, not a sign
anything is broken; this step only needs to run once, on a genuinely fresh
setup. (Confirmed empirically, 2026-08-14: a scratch database created twice
in a row behaved exactly this way.)

On Postgres **17 or 18**, also run `brew install pgvector` here — Homebrew's
bottle covers those versions and this is all you need. On Postgres **16**
(this machine, confirmed via `pg_config --version`), Homebrew's pgvector
bottle installs successfully but ships extension files only for 17/18 —
`CREATE EXTENSION vector` fails afterward with no earlier warning that
anything was wrong. Build from source against your actual Postgres instead:
```bash
git clone --branch v0.8.6 --depth 1 https://github.com/pgvector/pgvector.git
cd pgvector
make PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
make install PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
cd .. && rm -rf pgvector
```
Substitute your own `pg_config` path if not on Homebrew's `postgresql@16`.
This extension is now outside Homebrew's management — `brew upgrade` won't
touch it, and re-running these commands is how to update it later. (Root
cause confirmed empirically, 2026-08-19: `Cellar/pgvector/0.8.6/share/`
contained only `postgresql@17` and `postgresql@18` subdirectories, nothing
for 16; full account in
`docs/explanations/stage-5/step-01-dependencies-schema-tracing.md`.)

Either way, enable the extension per-database — building or installing it
only makes it *available*, it still has to be turned on in each database:
```bash
psql -d strategy_research -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -d strategy_research_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
```
This has to happen before the next step below: `Base.metadata.create_all()`
only issues `CREATE TABLE`, never `CREATE EXTENSION` — it has no mechanism
for arbitrary DDL — so `corpus_chunks`' `vector(384)` column will fail to
create if the extension isn't already enabled first.

```bash
python -m data_pipeline.db.init_db
```
Creates all tables via `Base.metadata.create_all()` against the URL in
`.env`'s `DATABASE_URL`. Safe to re-run — `create_all()`'s default
`checkfirst=True` only creates tables that don't already exist and never
touches existing ones. (Confirmed empirically, 2026-08-14: re-run directly
against the real `strategy_research` database — 4,164 real rows at the
time — with no error and no row-count change.)

```bash
alembic stamp head
```
Marks the database as caught up with Alembic's migration history, without
running any DDL — correct here because the schema was just created directly
by `init_db.py` above, not by a migration. Uses `settings.database_url`
(prod) by default; add `-x db=test` to target `strategy_research_test`
instead (`migrations/env.py` reads both from `data_pipeline.config.settings`,
never a hardcoded URL in `alembic.ini`).

### Schema changes (from now on)

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head          # prod
alembic -x db=test upgrade head   # test
```
`stamp head` is only for the baseline above, where the schema already
existed outside Alembic. Every real change from here on is a normal
`revision --autogenerate` + `upgrade head` — the autogenerated migration
will contain actual DDL this time, since there's a real diff to capture.

### Frontend setup (Stage 7 onward)

```bash
cd frontend
npm install
```
Node 25 / npm 11 confirmed working. `openapi-typescript` (the API-type
generator) requires TypeScript `^5.x` as a peer — `frontend/package.json`
pins `typescript: ~5.9.3` deliberately, not whatever the Vite scaffold's
own "latest" defaulted to at creation time, for exactly that reason.

```bash
PYTHONPATH=src .venv/bin/uvicorn api.app:app --port 8000   # separate terminal
npm run generate-api-types   # from frontend/, backend must be running
```
Regenerates `frontend/src/api/schema.gen.ts` from the real FastAPI
backend's own `/openapi.json` — every type in it traces to the actual
Pydantic response models in `src/api/schemas.py`, not hand-maintained.
**The generated file is committed** (like a lockfile): `npm install`
alone is enough to typecheck or build without a backend running. Re-run
`generate-api-types` by hand whenever a backend route or response shape
changes; a stale committed file only surfaces as a real type error
against actual usage, not silently.

```bash
npm run dev
```
Starts the Vite dev server on `http://localhost:5173` — the origin
`src/api/app.py`'s CORS middleware already allows, confirmed by an actual
cross-origin request during Component 3's own verification. To preview
inside this session's Browser pane instead of a personal terminal, use
`.claude/launch.json`'s `"frontend"` configuration.
