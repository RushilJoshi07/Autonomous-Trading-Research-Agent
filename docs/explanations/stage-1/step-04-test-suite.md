# Step 4 — Test Suite (Stage 1 Gate)

## 1. What this does

This component is the six-test suite that closes the Stage 1 gate. Before it
existed, the pipeline ran — but there was no proof that it ran correctly. The
tests assert four specific things the architecture promises:

1. **Data accuracy**: the value the DB stores matches an independently-verified
   real-world price, not just "what yfinance returned".
2. **Caching**: the incremental-start logic actually reads the cache before
   deciding where to fetch from.
3. **Per-ticker isolation**: a mid-run failure on one ticker does not corrupt or
   roll back the others, and leaves no partial write for the failed ticker.
4. **Raw/adjusted separation**: a corporate action updates adjusted prices and
   leaves raw prices byte-for-byte unchanged.
5. **Retry and FetchError typing**: the fetcher retries transient failures and
   raises the right exception type after exhaustion.

What this is NOT: a substitute for the Stage 2 and 5 sacred gates. This suite
verifies the data layer. It does not verify the backtester (Stage 2) or the
agent (Stage 5). The hardest correctness claims in this project are still ahead.

---

## 2. Every meaningful line explained

### conftest.py — shared test infrastructure

```python
@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(settings.database_url_test)
    create_schema(engine)
    yield engine
    engine.dispose()
```

`scope="session"` means this fixture runs once for the entire pytest session, not
once per test. Creating and schema-ing an engine is slow-ish (a TCP connection,
DDL round-trips). Running it 6 times would be waste. More importantly,
`create_schema` calls `create_all` — if it ran per-test, SQLAlchemy would try to
re-create existing tables, which is harmless but noisy. Session scope is the right
choice for anything that only needs to exist once per test run.

The alternative — `scope="function"` — would recreate the schema on every test.
That works, but `scope="module"` would also have worked (one engine per file, not
one per session). Session scope was chosen because the test database is a single
shared resource; there is no reason to tear it down and rebuild it between files.

`yield engine` is a generator fixture — pytest calls the code before `yield` to
set up, and the code after `yield` to tear down. `engine.dispose()` closes the
connection pool cleanly. Without it the connections would close anyway when the
process exits, but holding them open until then would leave the DB server with
dangling connections. Not a bug, but sloppy.

---

```python
@pytest.fixture
def db_session(test_engine):
    with test_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE ingestion_run_tickers, ingestion_runs, "
            "price_bars, ticker_metadata, corporate_actions_log CASCADE"
        ))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()
```

This fixture has no `scope=` argument, so it defaults to `scope="function"` —
it runs fresh for every test that requests it. Each test starts with an empty
database.

The truncation uses `TRUNCATE ... CASCADE`. A few things to unpack here:

`TRUNCATE` rather than `DELETE FROM`: both clear a table, but `TRUNCATE` is a
DDL operation that bypasses row-by-row deletion. On a large table it would be
much faster. On our small test tables the difference is negligible, but `TRUNCATE`
is the conventional tool for test cleanup and signals intent clearly.

`CASCADE`: the tables have foreign-key relationships. `ingestion_run_tickers.run_id`
references `ingestion_runs.id`. If you truncate `ingestion_runs` first without
CASCADE, the FK constraint on `ingestion_run_tickers` will fail because the
referenced rows are gone before the referencing rows are deleted. CASCADE tells
Postgres to propagate the truncation to referencing tables automatically. The
explicit list (all five tables in FK-safe order) is redundant with CASCADE but
makes the intent obvious to the reader.

The alternative — `DROP TABLE ... CREATE TABLE` — would work but is slower and
more fragile. The alternative — `DELETE FROM` — would also work but is slower on
larger tables.

The session is created fresh after the truncation (not before). This matters
because a session caches state internally. Starting a session before truncating
means the cache might reflect rows that the truncation then removes, leading to
confusing behaviour on the first DB read.

`session.close()` is called after `yield`. Without this, the session would stay
open until garbage collection. SQLAlchemy will emit a warning and close it
eventually, but explicit is better here.

---

```python
@pytest.fixture(autouse=True)
def patch_runner_session_factory(test_session_factory, monkeypatch):
    monkeypatch.setattr("data_pipeline.ingest.runner.SessionFactory", test_session_factory)
```

`autouse=True` means this fixture applies to every test in the directory without
any test having to explicitly request it. Every test that runs gets the runner
redirected to the test database automatically.

The alternative — making tests manually import and pass the session factory — would
work but requires every test that touches the runner to remember the setup step.
Missing it on one test would cause that test to write to the production database,
which would corrupt real data and be silent (the test might still pass).

`monkeypatch.setattr` with a string path targets the name in the module where the
function actually reads it (`data_pipeline.ingest.runner.SessionFactory`). If we
patched `data_pipeline.db.session.SessionFactory` instead, the runner would still
use its own local reference (which it imported at module load time) and the patch
would have no effect.

---

```python
def make_price_df(start_date, n_days=3):
    dates = pd.date_range(start=start_date, periods=n_days, freq="B")
    df = pd.DataFrame({...}, index=dates)
    df.index.name = "date"
    return df
```

A helper that produces a fake price DataFrame in the exact shape `fetch_prices`
returns. The `freq="B"` parameter means "business days" — it skips weekends. This
matters because the index is a DatetimeIndex and `upsert_price_bars` writes the
index values as date keys. If we used calendar days and a row fell on a weekend,
nothing would break, but the dates would be unrealistic. Using business days keeps
the fake data consistent with what real market data looks like.

`df.index.name = "date"` must match what `fetch_prices` sets. `upsert_price_bars`
uses `row["date"]` to find the date column. If the index name were anything else,
the upsert would fail with a KeyError.

---

### test_fetch.py — data accuracy against an independent source

```python
TOLERANCE = 0.01  # $0.01 — detects wrong data, not float formatting
```

The tolerance exists because the DB stores prices as `Numeric(18,6)` (exact
decimal), but yfinance returns Python floats (binary floating point). When we
convert `float → Decimal → DB → Decimal → float` for the comparison, we might
accumulate tiny rounding differences. A tolerance of $0.01 is large enough to
absorb any such noise while being small enough to catch a genuinely wrong value
(e.g., a price that differs because the ticker was wrong, or because the date was
off by one, or because yfinance returned adjusted rather than raw prices).

The alternative — exact equality — would occasionally produce spurious failures
when rounding harmlessly differs in the last decimal place.

```python
df = fetch_prices(ticker, start=target_date, end=target_date + timedelta(days=1))
```

yfinance treats `end` as exclusive. If `end == start`, the window is zero-length
and yfinance returns an empty DataFrame. We add one day to `end` so the window
covers exactly `[start, start+1)`, which includes `start`. This is not obvious
from the yfinance documentation and is a common source of off-by-one errors. The
`timedelta(days=1)` is what makes the test actually fetch any data at all.

The alternative — not passing `end` — would fetch from `target_date` to today.
That works but fetches far more data than needed for a golden-value test, wasting
network time.

---

### test_ingest.py — caching and per-ticker isolation

```python
def test_caching(db_session):
    calls: list[tuple] = []

    def _capture_fetch(ticker, start, end=None):
        calls.append((ticker, start))
        return make_price_df(start_date=start, n_days=2)

    with patch("data_pipeline.ingest.runner.fetch_prices", side_effect=_capture_fetch), \
         patch("data_pipeline.ingest.runner.fetch_metadata", return_value={}):
        ingest_daily(tickers)
        ingest_daily(tickers)

    assert second_start > first_start
```

The test captures the `start` argument on each `fetch_prices` call and asserts
that the second run's start is later than the first run's start. This directly
proves the cache-reading logic: if the runner were ignoring the cache and always
fetching from `_DEFAULT_START = date(2010, 1, 1)`, both calls would use the same
start date and the assertion would fail.

The alternative — asserting that `rows_written == 0` on the second run — was
rejected. If we use the same mock for both runs, the mock always returns data for
any `start` date we ask it about. So `rows_written` would be non-zero on the
second run too (the data is for different dates, so it's new). The assertion would
pass vacuously, proving nothing. The start-date invariant is the correct check
because it is the thing the caching mechanism actually controls.

---

```python
def test_partial_run_failure(db_session):
    def _side_effect(ticker, start, end=None):
        if ticker == "DDD":
            raise FetchError("simulated network timeout")
        return make_price_df(start_date=start, n_days=2)

    ...
    ddd_count = db_session.execute(
        select(func.count()).where(PriceBar.ticker == "DDD")
    ).scalar_one()
    assert ddd_count == 0, "DDD had a partial write — per-ticker isolation failed"
```

The `ddd_count == 0` assertion is the load-bearing check. It would fail if the
runner used a single transaction for all tickers: in that case, fetching would
succeed for AAA, BBB, CCC (writing their rows to the open transaction), then fail
for DDD (triggering a rollback that wipes AAA, BBB, CCC as well), then succeed
for EEE (but now there is no open transaction to write into, so this would likely
crash). Neither outcome is acceptable. Zero rows for DDD and non-zero for
everything else is the only correct result.

The `run.status == "partial_success"` assertion confirms the runner computed the
overall status correctly from the per-ticker rows. If the runner had set it to
`"success"` (ignoring failures), the check would catch that.

---

### test_corporate_actions.py — raw/adjusted separation

```python
assert float(bar.raw_close) == pytest.approx(_SEEDED_RAW_CLOSE, abs=0.01), (
    f"raw_close changed on {bar.date} — the raw/adj separation failed"
)
assert float(bar.adj_close) == _EXPECTED_ADJ_CLOSE_AFTER_SPLIT
```

Both sides of the assertion matter. Asserting only that `adj_close` changed would
tell us the upsert ran, but not whether `raw_close` was protected. Asserting only
that `raw_close` unchanged would not tell us whether the adjustment actually
applied. The test would pass even if the upsert silently no-oped. Both checks are
required; either alone is incomplete.

The seeded data deliberately sets `adj_close = raw_close = 400.0` (simulating a
cache populated before the split was known). This is important: if we seeded
`adj_close = 100.0` already, the test would not actually verify that the upsert
changed anything.

The mock returns `_make_post_split_adjusted_df()` — a DataFrame where raw columns
are unchanged (400.0) and adjusted columns are ÷4 (100.0). This mirrors what
yfinance actually returns after a split: it retroactively adjusts all historical
adj_* values to account for the split factor.

---

### test_retry.py — retry count and exception type

```python
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    from data_pipeline.fetch.prices import _history
    monkeypatch.setattr(_history.retry, "sleep", lambda _: None)
```

Tenacity configures `wait_exponential(min=4, max=60)` — meaning it would sleep
4 to 60 real seconds between retry attempts. Without this fixture, the test suite
would take 4–8 seconds per retry test (2 retries × minimum 4 seconds each).

The naive fix is `monkeypatch.setattr(time, "sleep", lambda _: None)`. This does
not work here. When `@retry_on_failure` decorates `_history`, it creates a
`Retrying` object and stores `time.sleep` in `self.sleep` as a direct function
reference — not a lookup through the `time` module. By the time the test patches
`time.sleep`, `Retrying.sleep` already holds the original `time.sleep` object.
Patching `time.sleep` changes the module attribute but doesn't update
`Retrying.sleep`. The sleep still runs.

The correct target is `_history.retry.sleep` — the attribute on the `Retrying`
instance itself. `monkeypatch.setattr` can patch any attribute on any Python
object, not just module-level names. This is the only way to reliably suppress
tenacity's sleep without patching tenacity's internals.

---

```python
def test_retries_on_transient_failure():
    mock_ticker.history.side_effect = [
        Exception("attempt 1 fails"),
        Exception("attempt 2 fails"),
        _GOOD_DF,
    ]
    with patch("data_pipeline.fetch.prices.yf.Ticker", return_value=mock_ticker):
        result = _history("AAPL", _START, _END, auto_adjust=False)
    assert mock_ticker.history.call_count == 3
```

This test calls `_history` directly, not `fetch_prices`. The distinction matters
because `fetch_prices` calls `_history` twice — once for raw prices and once for
adjusted. If we tested via `fetch_prices`, `call_count == 3` would be wrong: the
raw call would consume all three attempts, and we'd never get to the adjusted call.
The test would pass but be testing the wrong invariant (three retries on one of
two sub-calls, not three retries total).

By calling `_history` once and asserting `call_count == 3`, we test exactly what
we claim: the retry decorator makes three attempts on a single function call.

---

```python
def test_fetch_error_on_exhaustion():
    with patch("data_pipeline.fetch.prices.yf.Ticker", return_value=mock_ticker):
        with pytest.raises(FetchError):
            fetch_prices("AAPL", start=_START, end=_END)
    assert mock_ticker.history.call_count == 3
```

The exception type assertion is more important than the call count. The ingest
runner catches `FetchError` per ticker:
```python
except FetchError as exc:
    _log_ticker_error(run_id, ticker, exc)
```

If tenacity re-raised a bare `Exception` instead of `FetchError`, this except
clause would not catch it. The exception would propagate up, crash the loop, and
leave all subsequent tickers unprocessed with no error recorded for any of them.
The system would silently fail: no rows written, no `partial_success` status, just
a crash.

`FetchError` is raised by `fetch_prices`'s except handler, which wraps the
re-raised exception from tenacity (`reraise=True` in the tenacity decorator means
it re-raises the original exception, not a `RetryError`). `fetch_prices` catches
`Exception` and re-raises as `FetchError`. So the chain is:
`yfinance.history() raises Exception → tenacity re-raises Exception → fetch_prices
catches Exception, raises FetchError`.

---

## 3. Design decisions and rejected alternatives

### Decision: one test that calls real yfinance; all others mock

The golden-value test (`test_data_matches_known_source`) calls real yfinance. All
other tests use mocks. This was a deliberate split. If every test called real
yfinance, the suite would be slow, flaky (network failures), and non-deterministic
(yfinance is unofficial and can change its output format). If no test called real
yfinance, we'd never know whether the production data path actually works.

The one real test is the proof that the pipeline is connected. The remaining five
tests verify that the pipeline behaves correctly in controlled conditions. Both are
necessary; neither alone is sufficient.

The golden value is pinned in a CSV fixture rather than hard-coded in the test.
Pinning it in the test would mean the fixture and the assertion are in the same
file — if someone changes the expected value without understanding what it is,
there is no reminder that it came from an external source. The CSV has a `source`
and `retrieved_on` field that serve as documentation: this number was obtained
externally and should not be changed without re-verifying externally.

### Decision: TRUNCATE rather than test-specific rollback

An alternative test isolation strategy is to wrap each test in a database
transaction, then roll it back at the end. The test sees committed-looking data
but nothing actually lands in the DB. This is elegant and fast.

It was rejected here because it breaks per-ticker isolation testing. The runner
commits each ticker's transaction independently. If the test wraps everything in an
outer savepoint/transaction that it will roll back, the runner's inner commits
would be nested within that outer transaction. But the runner's rollback test
(`test_partial_run_failure`) specifically tests that one ticker's rollback does not
affect others — if all of them are inside an outer test transaction that never
commits, the test DB never reflects the committed state anyway and the assertion
becomes meaningless.

TRUNCATE + fresh session is less elegant but provably correct for what we need.

### Decision: scope="session" for test_engine, scope="function" for db_session

These different scopes serve different purposes. `test_engine` represents a
connection to the database — it's expensive to create and should be created once.
`db_session` represents a clean database state — it must be fresh for every test.
Mixing the scopes (session-scoped session, or function-scoped engine) would be
wrong in opposite directions: a function-scoped engine creates a new connection
pool six times, which is wasteful; a session-scoped session shares cached state
across tests, which would make test outcomes depend on execution order.

---

## 4. Concepts introduced

### What "test isolation" means in practice

Test isolation means each test must start from the same known state, and its
outcome must not depend on which tests ran before it or in what order. Without
isolation, tests pass or fail depending on execution order — a bug that produces
wrong data in test 3 causes test 5 to fail, even though test 5's code is correct.
When this happens, running tests individually passes; running them together fails.
This is one of the most common sources of hard-to-diagnose test failures.

TRUNCATE before each test is one way to enforce isolation. Session-scoped engine
with function-scoped sessions is another piece of it. The `autouse` fixture that
patches the runner's session factory ensures no test accidentally writes to the
production database, which would be a catastrophic isolation failure.

### Why mocking and golden-value tests serve different purposes

A mock replaces a dependency with a fake that returns whatever you tell it to.
Mocking lets you test a specific behaviour in isolation — "given that yfinance
returns X, does the runner do Y?" — without caring whether yfinance actually
returns X in reality.

A golden-value test uses real dependencies and a known-correct reference value.
It answers a different question: "does the full pipeline, end to end with real
data, produce values that match reality?" A codebase can have 100% mocked test
coverage and still be completely wrong when run against real data. The golden-value
test is the bridge between "it does what we told it to" and "it does what is true."

Both are necessary. All mocks, no golden values: the tests pass but the software
is untested against reality. All golden values, no mocks: the tests are slow,
flaky, and unable to precisely isolate failure causes.

### The `autouse` fixture pattern

`autouse=True` on a pytest fixture means "apply this fixture to every test in this
scope automatically." The scope is controlled by where the fixture is defined —
in `conftest.py`, it applies to all tests in the same directory and subdirectories.

This is the right pattern when the setup is always correct and there is no reason
for any test to opt out. The test database redirect (`patch_runner_session_factory`)
applies unconditionally: every test should run against the test database, with no
exceptions. If a test does need to opt out, that would itself be a red flag.

The alternative — making each test manually request the fixture — is correct if
some tests legitimately don't need it. Here they all do, so `autouse=True` removes
boilerplate without hiding information.

---

## 5. How the verification gate was satisfied

**Gate requirement**: data matches a known source; caching works; failures degrade
gracefully.

**"Data matches a known source"** — satisfied by `test_data_matches_known_source`.
An externally-verified price (AAPL 2024-01-02 raw_close = 185.64, verified against
an independent source) is stored in a fixture CSV. The test calls real yfinance,
writes the result to the test DB, reads it back, and asserts the difference is
under $0.01. This is not yfinance-vs-yfinance — the fixture value is not derived
from yfinance.

What this does NOT prove: that all prices are correct. One golden value confirms
the pipeline is connected and not systematically corrupting data (e.g., applying
split adjustments to raw prices, or storing adj values in raw columns). It does not
sample every ticker or date. Systematic corruption of a specific subset of data
would not be caught by a single-ticker, single-date test.

**"Caching works"** — satisfied by `test_caching`. After a first run, the second
run's start date is strictly later than the first's, proving it read the DB cache.
If the runner always fetched from `_DEFAULT_START`, both calls would use
`date(2010, 1, 1)` and the assertion would fail.

What this does NOT prove: that the incremental window is computed precisely
correctly in every edge case (e.g., gaps in the data, daylight-saving transitions,
tickers with no prior data). It proves the mechanism exists and functions in the
normal case.

**"Failures degrade gracefully"** — satisfied by `test_partial_run_failure`. DDD
raises, all others succeed. The assertions check all three required properties:
zero rows for DDD (no partial write), `partial_success` status, and successful
rows for the others.

What this does NOT prove: multiple simultaneous failures, failures at different
positions in the ticker list, or failures from exceptions other than FetchError.
Only FetchError is caught by the runner's per-ticker handler; a programming error
(e.g., AttributeError) would still crash the run.

**Corporate-action handling** — satisfied by `test_split_detection_and_readjustment`.
Both sides of the raw/adj assertion are checked. The seeded data is pre-split
(adj = raw), so the test actually verifies a transition, not just a static state.

**Retry and exception type** — satisfied by `test_retries_on_transient_failure` and
`test_fetch_error_on_exhaustion`. Call count and exception type both verified.

---

## 6. Interview defense

**Q: Why is there only one test that calls real yfinance? Doesn't that leave most
of the pipeline untested against reality?**

A: The golden-value test proves the full pipeline is connected to reality and not
systematically corrupting data. Once that is established, the remaining behaviour
(caching, isolation, error handling) can be tested precisely with mocks — because
we have already proven that the components under the mock are doing the right thing.
If we added a real-yfinance call to every test, we would add network flakiness and
retry delays without learning anything new about correctness. The one real test
plus five mocked tests is more rigorous than six real tests.

**Q: Why didn't you just use a test database transaction and roll it back instead
of TRUNCATE?**

A: The rollback approach breaks the per-ticker isolation test. The runner commits
each ticker's transaction independently. If the test wraps everything in an outer
transaction that never commits, the runner's commits are nested savepoints — and
we can't tell whether a ticker "committed" in the sense the test cares about (its
data is durable and not affected by another ticker's failure). TRUNCATE and a fresh
session give us a real committed state to inspect, which is the only way to prove
that successful tickers weren't rolled back when a later ticker failed.

**Q (hard): Your caching test uses a mock and asserts on the start argument. But
what if the runner's date arithmetic is off by one — say it fetches from
last_cached_date, not last_cached_date + 1 day — and the test still passes because
second_start > first_start is trivially true regardless?**

A: That is a real gap. The test proves the mechanism exists (the second start is
later than the first), not that it is precisely correct (exactly last_cached_date
+ 1 day). A more precise version would assert `second_start == last_cached_date
+ timedelta(days=1)` by querying the DB for the last cached date after the first
run. The current test was a deliberate tradeoff: precise enough to catch the
obvious failure (no caching at all), and not brittle enough to fail on edge cases
like a non-business-day boundary. If you want to argue this in an interview, name
the gap and name why you made the tradeoff — that is stronger than claiming the
test is complete.

---

## 7. What comes next and why

Stage 2 builds the backtesting engine on top of the data Stage 1 provides. The
backtester reads price bars from Postgres and never touches the network. If Stage
1's data is wrong in any of the ways tested here — corrupted by a failed upsert,
inconsistent because raw and adjusted prices got mixed, or stale because the cache
was not incremented correctly — the backtester will produce wrong results without
any error. The corruption is silent because the backtester has no way to know what
the correct prices are.

This is why Stage 1's gate matters as a precondition and not just nice-to-have
tests: Stage 2's correctness claims rest entirely on Stage 1 being trustworthy. A
wrong price bar produces a wrong signal produces a wrong trade produces a wrong
backtest result. The backtester cannot detect that. Only Stage 1's tests can.

The specifically dangerous failure is a split event introducing a fake price drop
in raw prices — a 4:1 split that makes AAPL look like it crashed 75% overnight.
A strategy would "short" the crash and "buy" the recovery. The backtested Sharpe
ratio would be enormous and completely fictitious. Stage 1's raw/adjusted separation
and the corporate-action test are what prevent that specific failure from making it
downstream.
