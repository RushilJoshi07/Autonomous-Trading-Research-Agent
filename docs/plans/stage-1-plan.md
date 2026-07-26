# Stage 1 — Data Pipeline

## Context

Stage 1 is the foundation everything else sits on: the backtester (Stage 2), the
strategy schema (Stage 3), and eventually the agent itself all read prices out of
Postgres and never touch the network directly. Per
[docs/architecture.md](../../../agentic-finance-platform/docs/architecture.md)
section 6 and
[.claude/rules/data-pipeline.md](../../../agentic-finance-platform/.claude/rules/data-pipeline.md),
the gate for this stage is: **data matches a known source, caching works, and
failures degrade gracefully** — no LLM involved.

The repo is currently empty except for docs and an empty venv. Postgres 16.14 is
running locally (Homebrew) and `strategy_research` exists but has no schema yet.

Universe for Stage 1: a small hand-picked list of ~15-20 tickers (the screener
tool that resolves a real universe doesn't exist until Stage 4).

Everything below is deterministic Python — no LLM calls, per "Stages 1-3 use no
LLM at all."

This revision answers four questions raised on the first draft: migration
tooling, transactional integrity on partial failure, an actual corporate-action
test, and a concrete (not circular) external data check.

## Components (build order — each one runnable and checkable before the next)

### 1. Project scaffolding
- `pyproject.toml` with pinned deps: `yfinance`, `sqlalchemy`, `psycopg2-binary`,
  `pydantic`, `pydantic-settings`, `tenacity` (retry/backoff — existing library,
  not hand-rolled), `pytest`.
- `.env` for `DATABASE_URL`, loaded via `pydantic-settings` so config is
  validated at startup, not read ad hoc. Add `.env` to `.gitignore`.
- Directory layout:
  ```
  src/data_pipeline/
    config.py            # Settings(BaseSettings) — DATABASE_URL, etc.
    db/
      models.py          # SQLAlchemy models
      session.py         # engine + session factory
      init_db.py          # create_all() — see Component 2
    universe.py           # the ~15-20 ticker list, documented as a placeholder
    fetch/
      client.py           # yfinance wrapper: retry/backoff via tenacity
      prices.py           # OHLCV history fetch
      metadata.py          # sector/industry/listing status fetch
      corporate_actions.py # splits/dividends fetch
    ingest/
      upsert.py           # writes fetched data into DB, dedupe on (ticker, date)
      corporate_actions.py # detects splits/dividends, re-fetches affected tickers
    cli.py                 # manual entry points: `ingest`, `backfill`, `check-actions`
  tests/data_pipeline/
    conftest.py            # test-DB fixture (see Testing below)
    fixtures/
      known_prices.csv      # hand-pinned external golden values (see Component 6)
    test_fetch.py
    test_ingest.py
    test_retry.py
    test_corporate_actions.py
  ```

### 2. Database schema — `create_all()`, not Alembic (revised)

**Original plan proposed Alembic from day one. On reflection, that's the wrong
call for this stage, and here's why:**

Alembic's actual value is evolving a schema *without losing data that's expensive
to regenerate*. Nothing in Stage 1's Postgres cache meets that bar — it's a cache
of yfinance, disposable and fully re-fetchable at will, and the schema itself is
still being iterated on inside this same stage. Setting up `alembic init`,
`env.py`, and an autogenerate workflow now adds real tooling overhead to protect
data that isn't worth protecting yet.

So: `db/init_db.py` calls `Base.metadata.create_all(engine)` — idempotent, and
`DROP DATABASE strategy_research_test` / recreate is a fine reset button during
development. **Alembic gets introduced at the point a schema change would
otherwise force dropping data you don't want to lose** — realistically the
Stage 1→2 boundary, once the backtester depends on a populated cache. At that
point, one `--autogenerate` revision against the then-current `create_all()`'d
schema becomes the baseline migration. Nothing is lost by waiting — it's a
15-minute setup whenever it's actually needed.

Tables (unchanged from the original draft):
- `price_bars` — ticker, date, raw OHLCV, adjusted OHLCV, `fetched_at`. Raw and
  adjusted are **separate columns** — this is what makes corporate-action
  re-fetches non-destructive to history.
- `ticker_metadata` — ticker, sector, industry, listing status, `updated_at`.
- `corporate_actions_log` — ticker, action type, date, value, `detected_at`.
  Append-only audit trail.
- `ingestion_runs` — run id, started_at, finished_at, overall status
  (`success` / `partial_success` / `failed`).
- `ingestion_run_tickers` (new — see Component 4) — run_id (FK), ticker, status,
  error, rows_written. Per-ticker granularity for a run.

### 3. Fetcher (yfinance wrapper)
- One retry-wrapped client (`tenacity`, exponential backoff, bounded attempts)
  all fetch functions go through.
- `prices.py`: full history + incremental fetch. `metadata.py`: sector/industry/
  listing status. `corporate_actions.py`: splits/dividends via yfinance's
  actions endpoint.
- On exhausted retries: raise a typed exception the ingest layer catches per
  ticker (see below) — never a bare crash, never a silent partial write.

### 4. Ingest / cache layer — transactional semantics (revised)

**Original plan didn't specify what happens if ingestion dies partway through
the ticker list. Specified now:**

- **Transaction granularity is per-ticker, not per-run and not per-row.** Each
  ticker's fetch-and-write is wrapped in its own DB transaction: either that
  ticker's full batch of new/updated bars commits, or none of it does (no
  half-written date range for one ticker). One transaction per ticker, not one
  giant transaction for the whole batch — a failure on ticker 14 of 20 must not
  roll back tickers 1-13.
- **The ingest loop catches exceptions per ticker and continues.** A single bad,
  delisted, or rate-limited ticker logs a `failed` row to
  `ingestion_run_tickers` and the loop moves on — it does not abort the run for
  the remaining tickers.
- **`ingestion_runs.status`** is computed from the child rows once the run
  finishes: `success` if all tickers succeeded, `partial_success` if some
  failed, `failed` if all failed.
- Corporate-action handling (`ingest/corporate_actions.py`): on detecting a new
  split/dividend not yet in `corporate_actions_log`, re-fetch and overwrite
  **adjusted** prices for that ticker's affected historical range in one
  transaction; **raw stays untouched**; write one row to
  `corporate_actions_log`.
- Functions are plain callables (`ingest_daily(tickers)`,
  `check_corporate_actions(tickers)`, `full_refetch(tickers)`) invoked manually
  via `cli.py`. Wiring these to a scheduler is Stage 8 — out of scope here.

### 5. Survivorship-bias documentation
A short note in `universe.py` and the stage's explanation doc: Stage 1 uses
today's hand-picked tickers, not point-in-time membership — no delisted/bankrupt
names included. This is the documented limitation the rules require, not solved
here.

### 6. Testing / verification (the actual Stage 1 gate)

Dedicated `strategy_research_test` Postgres database (same local instance),
schema created via `init_db.create_all()` in a pytest fixture, torn down/recreated
per test session.

**a. Data matches a known source — fixed instead of circular.**
The original draft compared freshly-fetched yfinance data against DB-cached
data, which is yfinance-vs-yfinance and proves nothing about correctness against
reality. Real fix:
- Ticker **AAPL**, date **2024-01-02** — an ordinary trading day, no earnings or
  split nearby, and after AAPL's last split (2020-08-31), so its **raw**
  (unadjusted) close is permanently stable — nothing retroactively changes it.
- Compare against **raw close specifically, not adjusted close.** Adjusted close
  is *supposed* to change as new splits occur, so a golden test pinned to it
  would break later for reasons unrelated to a real bug.
- I could not obtain this number from a live independent source in this
  sandbox — Stooq's CSV endpoint, Nasdaq's historical API, and WSJ's download
  endpoint are all bot/JS-blocked here. Rather than guess a number and label it
  "verified," this needs a one-time manual step from you: open AAPL's
  2024-01-02 historical close on any source independent of Yahoo (WSJ, Nasdaq,
  Google Finance, or your brokerage) and drop it into
  `tests/data_pipeline/fixtures/known_prices.csv` as
  `ticker,date,raw_close,source,retrieved_on`. The test reads that fixture and
  asserts the DB value matches — no live scraping in the test itself, since
  these sites actively block scripted access and a "golden" value that
  re-fetches on every run isn't actually golden.

**b. Caching works.** Call `ingest_daily` twice; second call must not re-fetch
bars already cached (patch the yfinance client, assert it's not called for
already-covered dates).

**c. Graceful failure, with the partial-write case actually covered.**
`test_ingest.py::test_partial_run_failure` — mock the yfinance client to succeed
for tickers 1-3, raise on ticker 4, succeed for ticker 5. Assert:
tickers 1, 2, 3, 5 have committed rows in `price_bars`; ticker 4 has **zero**
rows in `price_bars` (proving no partial write) and one `failed` row in
`ingestion_run_tickers` with the error recorded; `ingestion_runs.status` ==
`partial_success`.

**d. Corporate-action handling, actually tested against a known split.**
`test_corporate_actions.py::test_split_detection_and_readjustment` — uses
**AAPL's real 4-for-1 split on 2020-08-31** as fixture data:
1. Seed `price_bars` with AAPL rows spanning before 2020-08-31, with adjusted
   prices computed *as if the split were not yet known* (simulating "cached
   before the split happened"). Leave `corporate_actions_log` empty for AAPL.
2. Run `check_corporate_actions(["AAPL"])`.
3. Assert: one new row lands in `corporate_actions_log`
   (ticker=AAPL, type=split, date=2020-08-31, value=4.0); `adjusted_close` for
   the pre-split rows is rewritten to reflect the 4:1 factor (~1/4 of the old
   cached value); **`raw_close` for those same rows is byte-for-byte
   unchanged** — this is the assertion that actually proves raw/adjusted
   separation does its job, not just that the columns exist.

## Explanations checkpoint

Per the working agreement, invoke `explanation-writer` after each component
functions (schema created, fetcher working, cache/upsert functioning with
transactional per-ticker semantics, retry logic in, corporate-action handling
verified) — not just at the end of the stage. Stage synthesis fires once
Component 6 passes in full.

## Verification (end-to-end, before calling Stage 1 done)

1. `python -m data_pipeline.db.init_db` against `strategy_research` creates all
   tables cleanly.
2. `python -m data_pipeline.cli ingest` against the real DB populates
   `price_bars`/`ticker_metadata` for the ~15-20 ticker list.
3. `pytest tests/data_pipeline` passes — including the four checks in
   Component 6, not just the happy path.
4. Manual one-time step: fill in `tests/data_pipeline/fixtures/known_prices.csv`
   with a hand-verified AAPL 2024-01-02 raw close from an independent source
   before test 6a can pass.

## Note on this plan file's location

Plan-mode restricts me to editing only the designated plan file
(`~/.claude/plans/purring-hugging-popcorn.md`) while planning is in progress.
Once you approve, my first action will be writing this content to
`docs/plans/stage-1-plan.md` in the repo as requested, before any other work
starts.
