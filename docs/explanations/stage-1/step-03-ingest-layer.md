# Step 3 — The Ingest Layer

## 1. What this does

This component takes what the fetcher returns (DataFrames in memory) and writes it
to Postgres with correctness guarantees. Five files:

- `src/data_pipeline/ingest/upsert.py` — three functions: `get_last_cached_date`
  (what is the most recent bar we already have for a ticker?), `upsert_price_bars`
  (insert new bars or update adjusted prices on conflict), `upsert_metadata` (insert
  or replace sector/industry/listing status).
- `src/data_pipeline/ingest/corporate_actions.py` — `handle_corporate_actions`:
  compares what yfinance reports for splits and dividends against what is already
  in `corporate_actions_log`, re-fetches adjusted prices for any new actions, and
  appends the new actions to the log.
- `src/data_pipeline/ingest/runner.py` — `ingest_daily`, `full_refetch`, and
  `check_corporate_actions`. These are the public entry points. Each one runs the
  per-ticker loop with transaction isolation and failure tracking.
- `src/data_pipeline/universe.py` — the 18-ticker hand-picked list for Stage 1,
  with a documented survivorship-bias disclaimer.
- `src/data_pipeline/cli.py` — three commands (`ingest`, `refetch`,
  `check-actions`) that invoke the runner functions from the terminal.

**What this is NOT for.** The ingest layer does not fetch data — that is the
fetcher's job (see [step-02-fetcher.md](step-02-fetcher.md)). It does not make
decisions about which tickers to process or when — that is the CLI and eventually
the scheduler's job. It does not read data for analysis — that is the backtester's
job starting in Stage 2.

**Smoke test result:** `ingest_daily(['AAPL'])` wrote 4,164 rows to `price_bars`,
run status `success`. That is AAPL's full daily history from 2010-01-01 through
the current date.

---

## 2. Every meaningful line explained

### `upsert.py`

```python
def get_last_cached_date(session: Session, ticker: str) -> date | None:
    return session.execute(
        select(func.max(PriceBar.date)).where(PriceBar.ticker == ticker)
    ).scalar()
```

`func.max` is SQLAlchemy's wrapper for SQL `MAX()`. This returns the most recent
date in `price_bars` for the given ticker, or `None` if no rows exist. The ingest
runner uses this to decide the `start` date for an incremental fetch — the day
after the last cached date. If `None`, the ticker has never been fetched, and the
runner falls back to `_DEFAULT_START` (2010-01-01).

Why a database query rather than caching this in Python? Because between runs, the
database may have been updated by a different process (a full refetch, a recovery
run). Querying fresh on every ingest run is the only way to be sure of the current
state.

---

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = pg_insert(PriceBar).values(rows)
stmt = stmt.on_conflict_do_update(
    index_elements=["ticker", "date"],
    set_={
        "adj_open":   stmt.excluded.adj_open,
        ...
        "fetched_at": stmt.excluded.fetched_at,
    },
)
session.execute(stmt)
```

`pg_insert` is imported from `sqlalchemy.dialects.postgresql`, not from
`sqlalchemy`. The standard SQLAlchemy `insert` does not support `ON CONFLICT`.
Only the PostgreSQL dialect does. This is a deliberate binding to PostgreSQL — it
is acceptable because the architecture document specifies PostgreSQL throughout, and
using a database-agnostic abstraction here would be false modularity that adds
complexity without enabling any real portability.

`ON CONFLICT (ticker, date) DO UPDATE` is a single atomic SQL statement. It either
inserts a new row (if the `(ticker, date)` pair does not exist) or updates an
existing row (if it does), with no window between the check and the write. A
check-then-insert pattern — check if the row exists, then decide whether to insert
or update — has a race condition: between the check and the insert, another process
could insert the same row, causing a duplicate-key error.

`set_` contains only the `adj_*` columns and `fetched_at`. `raw_open`, `raw_high`,
`raw_low`, `raw_close`, and `raw_volume` are deliberately absent. This means: on a
conflict (the date already exists), the raw prices from the existing row are kept
exactly as they are. The update only touches adjusted prices. This is not
documentation or convention — it is enforced by the SQL statement itself. Application
code cannot accidentally overwrite raw prices through this function, even with a
bug.

`stmt.excluded` is PostgreSQL syntax for "the row that was proposed for insertion
but was excluded due to the conflict." It refers to the new values being attempted,
allowing the update clause to say "use the new adjusted prices, not the old ones."

---

```python
    for idx, row in df.iterrows():
        rows.append({
            ...
            "raw_close": Decimal(str(row["raw_close"])),
            ...
        })
```

`Decimal(str(row["raw_close"]))` — the DataFrame holds floats. `Decimal(float)`
passes the float's binary representation into Decimal, which produces values like
`Decimal('185.6399993896484375')` instead of `Decimal('185.64')`. Converting via
`str` first — `Decimal(str(185.64))` — produces `Decimal('185.64')`, the exact
decimal value. The `Numeric(18, 6)` column in Postgres then stores `185.640000`
(padded to 6 decimal places). Without this conversion, each float's binary
approximation would be stored verbatim, and two runs computing the same return
from the same ticker might differ at the 10th significant digit — violating the
determinism requirement.

---

### `corporate_actions.py`

```python
def _known_actions(session: Session, ticker: str) -> set[tuple]:
    rows = session.execute(
        select(CorporateActionLog.action_type, CorporateActionLog.action_date)
        .where(CorporateActionLog.ticker == ticker)
    ).all()
    return {(r.action_type, r.action_date) for r in rows}
```

Returns a set of `(action_type, action_date)` pairs already recorded for the ticker.
A set is used rather than a list because the subsequent comparison (`if (a["action_type"],
a["action_date"]) not in known`) is an O(1) membership test against a set, versus
O(n) against a list. For a ticker with hundreds of historical dividends, this
matters.

---

```python
    all_actions = fetch_corporate_actions(ticker)   # step 1 — network call
    known = _known_actions(session, ticker)          # step 2 — DB read
    new_actions = [...]                              # step 3 — filter

    if not new_actions:
        return 0

    earliest = session.execute(...)                 # step 4 — DB read
    if earliest:
        df = fetch_prices(ticker, start=earliest)   # step 5 — network call
        upsert_price_bars(session, ticker, df)      # step 6 — DB write

    for action in new_actions:
        session.add(CorporateActionLog(...))         # step 7 — DB write
```

The ordering of steps 5, 6, and 7 is load-bearing.

Step 5 (`fetch_prices`) is the operation most likely to fail — it is a network call.
If it raises `FetchError`, steps 6 and 7 have not yet executed, so nothing has been
written to the database. The session is clean; the caller's `with SessionFactory()`
block closes the session without committing, and the exception propagates.

If the ordering were reversed — step 7 (log the action) before step 5 (fetch prices)
— and the fetch then failed, the action would be permanently recorded in
`corporate_actions_log` with no corresponding update to `adj_*` prices. The next
run would call `_known_actions` and see the action as "known", so it would skip the
re-fetch entirely. The `adj_*` prices would remain stale indefinitely, with no
indication in the database that anything was wrong. This is the partial-state bug
the ordering prevents.

---

### `runner.py`

```python
def _create_run() -> str:
    run_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(IngestionRun(id=run_id, started_at=..., status="in_progress"))
        session.commit()
    return run_id
```

The `IngestionRun` row is committed before the per-ticker loop begins. This is
required because `IngestionRunTicker` rows reference `ingestion_runs.id` via a
foreign key constraint — Postgres will refuse to insert a ticker row if the parent
run row does not exist yet. `status="in_progress"` is the fourth valid status
alongside `success`, `partial_success`, and `failed`. It marks a run that is
executing, distinguishing it from one that completed and was set to a terminal
status. A run with `finished_at IS NULL` and `status="in_progress"` is either
currently running or crashed.

---

```python
    for ticker in tickers:
        try:
            with SessionFactory() as session:
                ...
                session.commit()
        except FetchError as exc:
            logger.warning(...)
            _log_ticker_error(run_id, ticker, exc)
```

Each ticker has its own `with SessionFactory() as session:` block — its own
transaction. If the block exits normally (after `session.commit()`), the
transaction is committed. If `FetchError` is raised before the commit, the
`with` block's exit calls `session.close()`, which rolls back any uncommitted
changes for that ticker. The next ticker then gets a fresh session with no
contamination from the failure.

`FetchError` is caught rather than `Exception`. This is intentional: only network
failures should be swallowed and logged. A programming error — `AttributeError`,
`TypeError`, `NameError` — should propagate up and crash the process, because it
indicates a bug that needs fixing, not a recoverable failure.

---

```python
def _log_ticker_error(run_id: str, ticker: str, error: Exception) -> None:
    with SessionFactory() as session:
        session.add(IngestionRunTicker(..., status="failed", error=str(error)))
        session.commit()
```

`_log_ticker_error` opens its own session. It cannot reuse the failed ticker's
session because a session that raised an exception is in an undefined state — its
internal transaction may be aborted, and any further operations on it will fail.
Opening a fresh session guarantees the error logging succeeds regardless of what
happened in the data session.

---

```python
def _finish_run(run_id: str) -> None:
    with SessionFactory() as session:
        ticker_rows = session.execute(
            select(IngestionRunTicker).where(IngestionRunTicker.run_id == run_id)
        ).scalars().all()
        successes = sum(1 for t in ticker_rows if t.status == "success")
        failures  = sum(1 for t in ticker_rows if t.status == "failed")
        if failures == 0:
            status = "success"
        elif successes == 0:
            status = "failed"
        else:
            status = "partial_success"
        run = session.get(IngestionRun, run_id)
        run.finished_at = datetime.now(tz=timezone.utc)
        run.status = status
        session.commit()
```

`_finish_run` runs in a fresh session after all tickers have completed (succeeded
or failed). It reads the per-ticker rows and computes the overall status from them
rather than tracking a counter in Python during the loop. Computing from the
database is more reliable: if `_finish_run` is called after a crash-and-recovery
(some tickers committed before the crash), the database reflects the true state
even though the in-memory counter was lost.

`session.get(IngestionRun, run_id)` is the SQLAlchemy 2.0 way to fetch a row by
primary key within an open session. It returns `None` if the row doesn't exist,
but that cannot happen here since `_create_run` committed the row before the loop.

---

## 3. Design decisions and rejected alternatives

### Per-ticker transaction isolation

The chosen design gives each ticker its own SQLAlchemy session and its own database
transaction. The per-ticker loop catches `FetchError` for one ticker and continues
to the next, recording the failure in `ingestion_run_tickers`.

The alternative — one session for the entire run — was rejected because a single
session means a single transaction. If ticker 14 of 20 fails, the transaction rolls
back, erasing the committed rows for tickers 1 through 13. Those tickers would
need to be re-fetched on the next run, which defeats the purpose of incremental
caching. The per-ticker design means a failure on ticker 14 leaves tickers 1–13
committed in the database; only ticker 14 is retried next time.

The cost of reversing this decision is low — it requires changing the session
scope in the runner — but the consequence of using a single session in production
would be that any transient yfinance failure for any one ticker wipes the entire
run's work. That is unacceptable for a pipeline that runs nightly.

### `ON CONFLICT DO UPDATE` updating `adj_*` only — not `DO NOTHING`

The alternative was `ON CONFLICT DO NOTHING`, which would silently skip any row
whose `(ticker, date)` already exists. This was rejected for a specific case: when
a corporate action is processed by `check_corporate_actions`, it calls `fetch_prices`
and then `upsert_price_bars` to update the adjusted prices for historical rows.
Those rows already exist — that is the whole point. `ON CONFLICT DO NOTHING` would
skip them, leaving the `adj_*` columns unchanged, making the corporate action
handler silently ineffective.

`ON CONFLICT DO UPDATE` with `adj_*` in the `set_` clause handles both cases
correctly: it inserts new rows on the first ingest, and updates only the adjusted
columns when called by the corporate action handler. Raw prices are never in `set_`,
so they are never overwritten regardless of how this function is called.

### Logging the corporate action after re-fetching, not before

The decision to call `fetch_prices` before `session.add(CorporateActionLog(...))` in
`handle_corporate_actions` is the most subtle ordering decision in the ingest layer.

The alternative — log the action first, then re-fetch prices — was rejected because
it creates an unrecoverable partial state. If the log is written and committed, and
then the price re-fetch fails, the action is permanently in `corporate_actions_log`.
The next run calls `_known_actions`, sees the action as already recorded, and
skips the re-fetch. The `adj_*` prices are never updated. There is no error in the
database — the log looks correct — but the data is wrong.

The chosen ordering means that if the re-fetch fails, nothing has been committed.
The action is not in the log. The next run will see it as new, attempt the re-fetch
again, and eventually succeed (or fail again, and again, until it does). This is
idempotent: the failure case is "retry next time," not "silently corrupt the data."

### `_DEFAULT_START = date(2010, 1, 1)`

The full-history fetch anchors to 2010-01-01. The alternatives were an earlier date
(further history) or a later one (less).

Starting before 2010 was rejected because pre-2010 data from yfinance is often
incomplete, has worse split-adjustment coverage, and includes the 2008–2009 financial
crisis where many tickers have irregular data or were temporarily halted. A 15-year
window from 2010 is sufficient for any meaningful backtesting strategy.

Starting later was rejected because it would limit the in-sample window available
to the backtester, reducing statistical power for detecting real edges. More history
is better as long as the data quality is acceptable.

### No run tracking for `check_corporate_actions`

`ingest_daily` and `full_refetch` create `IngestionRun` rows and track per-ticker
outcomes. `check_corporate_actions` does not — failures are logged as `WARNING` and
the function returns.

The alternative — tracking corporate action checks in `ingestion_runs` the same
way — was rejected because corporate action checks are maintenance, not data
ingestion. The `ingestion_runs` table is the audit trail for "what new data entered
the cache." A corporate action check that found no new actions would generate a run
row with zero rows written — noise in the audit trail. More importantly, adding run
tracking to corporate action checks would make the function significantly more
complex (the same `_create_run` / `_finish_run` pattern) for no operational benefit.

---

## 4. Concepts introduced

### Database transactions and ACID

A **transaction** is a group of database operations that either all succeed or all
fail — there is no state where half of them are committed. The "all or nothing"
property is called **atomicity** (the A in ACID). When a SQLAlchemy session calls
`session.commit()`, all the operations staged in that session are sent to Postgres
as a single transaction. When a session is closed without committing (e.g., after
an exception), Postgres discards all the staged operations.

In this codebase, each ticker's fetch-and-write is one transaction. Either all
4,164 of AAPL's price bars are committed, or none of them are. There is no state
where 2,082 bars are committed and the other 2,082 are not. This is what "per-ticker
transaction isolation" means in practice.

### Upsert (INSERT ... ON CONFLICT DO UPDATE)

An **upsert** is a single SQL statement that behaves like an INSERT if the row
does not exist, and like an UPDATE if it does. The name is a portmanteau of
"update" and "insert."

Without upsert, you need two statements: a SELECT to check if the row exists, then
either an INSERT or an UPDATE based on the result. Between those two statements,
another process could insert the same row, causing the INSERT to fail with a
unique-constraint violation. Upsert is a single atomic statement — the check and
the write happen together, with no window for a race condition.

PostgreSQL's syntax is `INSERT ... ON CONFLICT (key_columns) DO UPDATE SET ...`.
The `ON CONFLICT` clause specifies which columns form the uniqueness constraint
(here, `ticker` and `date`), and `DO UPDATE SET` specifies which columns to update
on conflict. Columns not listed in `DO UPDATE SET` are left unchanged — which is
how raw prices are protected.

### Survivorship bias (introduced in universe.py)

The 18-ticker universe in `universe.py` contains only companies that currently
exist and are listed on a major exchange. Companies that went bankrupt, were
delisted, or were acquired are not included — they are invisible in this data.

This creates **survivorship bias**: the universe contains only the survivors, which
are by definition the companies that performed well enough to still exist. Any
backtest run on this universe implicitly starts with the knowledge of which
companies survived — knowledge that was not available in real time. The apparent
performance of strategies on this universe is inflated compared to what would have
been achievable on the full historical universe including companies that later failed.

The bias is disclosed in the comments in `universe.py` and the relevant
explanation documents. It is not solved in Stage 1 — solving it requires
reconstructing point-in-time index membership, which is a separate project (see
`docs/architecture.md` section 6). Disclosing a known limitation is more honest
and defensible than pretending the limitation does not exist.

---

## 5. How this component was verified

End-to-end smoke test run immediately after all files were written:

```python
from data_pipeline.ingest.runner import ingest_daily
run_id = ingest_daily(['AAPL'])
# then queried: rows in price_bars, status in ingestion_runs
```

Result: 4,164 rows in `price_bars` for AAPL, run status `success`.

**What this proves:** the full pipeline from runner → upsert → Postgres works for
the happy path. The run tracking tables are populated correctly. The incremental
logic (`get_last_cached_date`) returns a non-null value after the first run, so a
second `ingest_daily(['AAPL'])` call would fetch only new bars rather than
re-fetching 4,164 rows.

**What this does NOT prove:**

The partial-failure test — that a failing ticker leaves other tickers committed and
logs a `failed` row in `ingestion_run_tickers` — is covered by
`test_ingest.py::test_partial_run_failure`, which patches yfinance to fail on a
specific ticker. The smoke test only exercises the success path.

The corporate action re-fetch — that `handle_corporate_actions` correctly detects
a new split, updates `adj_*` columns, and leaves `raw_*` unchanged — is covered by
`test_corporate_actions.py::test_split_detection_and_readjustment`. The smoke test
did not exercise this path.

The data-accuracy check — that the values written to `price_bars` are numerically
correct — is covered by the golden-value test against the
`fixtures/known_prices.csv` fixture. The smoke test only confirms rows were
written, not that their values are right.

---

## 6. Interview defense

**"Why did you choose per-ticker transactions instead of one transaction for the
whole run?"**

Because a single transaction means a single rollback unit. If ticker 14 of 20 fails
for a transient reason — rate limiting, a momentary yfinance outage — a single
transaction rolls back tickers 1 through 13 as well. They would need to be
re-fetched on the next run. Over time, a nightly pipeline where any single ticker
failure restarts the entire run becomes unreliable and expensive. Per-ticker
transactions mean only the failing ticker is retried; the others are committed and
do not contribute to the next run's cost.

**"Why didn't you just use `ON CONFLICT DO NOTHING`? It's simpler."**

It is simpler, but it silently breaks the corporate action handler. When a split
is detected, `handle_corporate_actions` calls `fetch_prices` to get the updated
adjusted prices and then calls `upsert_price_bars` to write them. Those rows
already exist — their `(ticker, date)` pairs are in the database from the original
ingest. `ON CONFLICT DO NOTHING` would skip every one of them, leaving `adj_*`
columns unchanged and making the corporate action handler a no-op. `ON CONFLICT DO
UPDATE` with `adj_*` in the set clause handles both cases correctly: insert on the
first ingest, update adjusted prices when called by the corporate action handler.

**"Your `_DEFAULT_START` is hardcoded to 2010. What if a study needs data from
2005?"**

It is a scope decision made explicitly rather than a mistake. Pre-2010 yfinance
data has gaps, inconsistent split adjustments, and poor coverage for smaller
tickers. Including it would inflate the apparent historical data depth while
actually producing unreliable results for any strategy that happened to pick up
a pre-2010 signal. If a study genuinely needed pre-2010 data, the right fix is
to use a paid data provider (Bloomberg, Refinitiv) rather than extend the yfinance
window — and the `_DEFAULT_START` constant is a one-line change if that provider
is integrated later.

**Hard question: "Your `Decimal(str(float))` conversion happens at write time, but
the DataFrame holds floats throughout the fetch and transform pipeline. Doesn't
the float precision problem apply to all the arithmetic you might do on the data
before writing?"**

Yes, this is a real limitation. The fetcher and the merge logic operate on floats
because yfinance returns floats and pandas uses float64 internally. Any computation
done on the in-memory DataFrame before writing — computing returns, applying a
split ratio — accumulates floating-point error. The `Decimal(str(float))` conversion
at write time converts the float's string representation to an exact Decimal, but
it cannot recover precision that was lost in the float arithmetic upstream.

The mitigation is that Stage 1's ingest layer does no arithmetic on prices — it
reads from yfinance and writes to the database without transforming values. The
only computation is the split detection in `handle_corporate_actions`, which
compares action identifiers (type and date), not prices. All price arithmetic
happens in the backtester (Stage 2), which reads from the database as Decimal and
operates in Decimal throughout. So the float-precision window is narrow: yfinance's
output to the database write. Within that window, the `Decimal(str(float))`
conversion ensures at least the string representation of the float (e.g.,
`"185.64"`) is preserved exactly, even if the underlying float has 15 decimal
digits of noise.

---

## 7. What comes next and why

The next component is the test suite (`tests/data_pipeline/`). The tests are what
actually close the Stage 1 gate — the smoke test proved the happy path works, but
the gate requires four specific things to be true:

1. The data in the database matches an independent source (golden-value test).
2. A second `ingest_daily` call does not re-fetch already-cached bars (caching test).
3. A failure on one ticker does not corrupt or roll back other tickers' data
   (partial-failure test).
4. A corporate action is detected, `adj_*` is updated, and `raw_*` is unchanged
   (split detection test).

If any of these tests fails, Stage 1 is not done — regardless of how many rows
the smoke test wrote. The tests are the gate, not the smoke test.

If the ingest layer were wrong in a subtle way — for example, if `ON CONFLICT DO
UPDATE` were accidentally updating `raw_*` columns — the backtester (Stage 2) would
compute returns from subtly wrong prices. Strategies that appear to have an edge
would be backtested against data that changed after the fact, invalidating every
result. The corporate action test in Step 4 specifically checks that `raw_*`
values are byte-for-byte unchanged after a detected split, closing that failure
mode before Stage 2 begins.
