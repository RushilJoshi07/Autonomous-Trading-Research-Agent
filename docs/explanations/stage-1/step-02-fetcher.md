# Step 2 — The Fetcher

## 1. What this does

This component is the only part of the codebase that talks to yfinance. Four files:

- `src/data_pipeline/fetch/client.py` — a retry decorator (`retry_on_failure`) and
  a typed exception (`FetchError`). Every yfinance call goes through the decorator;
  every public fetch function raises `FetchError` on exhaustion.
- `src/data_pipeline/fetch/prices.py` — `fetch_prices(ticker, start, end)` fetches
  OHLCV history and returns a DataFrame with eleven columns: `raw_open` through
  `raw_volume`, `adj_open` through `adj_volume`, and `fetched_at`.
- `src/data_pipeline/fetch/metadata.py` — `fetch_metadata(ticker)` returns a dict
  with `sector`, `industry`, and `listing_status`.
- `src/data_pipeline/fetch/corporate_actions.py` — `fetch_corporate_actions(ticker)`
  returns a flat list of split and dividend records, each a dict with `action_type`,
  `action_date`, and `value`.

**What this is NOT for.** The fetcher does not write to the database — that is the
ingest layer's job (see `src/data_pipeline/ingest/`). It does not decide which
tickers to fetch or when — that is the `cli.py` and eventually the scheduler's job.
It does not apply split adjustments to historical data — that is the corporate
action handler in the ingest layer. The fetcher's only job is to reach out to
yfinance and hand back clean, typed data.

---

## 2. Every meaningful line explained

### `client.py`

```python
class FetchError(Exception):
    """Raised when a yfinance fetch fails after all retries are exhausted."""
```

A typed exception class. The ingest layer catches `FetchError` specifically, per
ticker, and logs the failure rather than crashing the whole run. If this were a
bare `Exception`, the ingest layer would need to catch `Exception` — which risks
accidentally swallowing programming errors (like `AttributeError` from a typo)
that should crash and be fixed, not silently logged as fetch failures.

```python
retry_on_failure = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
```

`retry_on_failure` is a decorator object — you apply it with `@retry_on_failure`
on any function, and tenacity wraps that function so it retries automatically on
any exception.

`stop_after_attempt(3)` — three total attempts (one original call plus two retries).
More than three gives diminishing returns against a rate-limited API; fewer means
transient failures (a momentary yfinance outage) become ingest failures.

`wait_exponential(multiplier=1, min=4, max=60)` — after the first failure, wait
4 seconds; after the second, 16 seconds; the cap at 60 seconds prevents the wait
from growing indefinitely. Exponential backoff is standard practice for unofficial
APIs: a flat `sleep(2)` hits the API at a fixed cadence that a rate limiter will
continue blocking; exponential spacing gives the API time to recover.

`reraise=True` — when all retries are exhausted, re-raise the original exception
rather than wrapping it in tenacity's own `RetryError`. This matters because the
public fetch functions (in `prices.py` etc.) catch and convert that exception into
`FetchError`. If tenacity wrapped it in `RetryError` first, the public functions
would need to know about tenacity's internal exception type, creating an unwanted
dependency.

`before_sleep=before_sleep_log(logger, logging.WARNING)` — tenacity logs a WARNING
before each sleep, showing which exception occurred and how long the backoff is.
Without this, retries happen silently and there is no record of why an ingest run
took longer than expected.

---

### `prices.py`

```python
@retry_on_failure
def _history(ticker: str, start: date, end: date | None, auto_adjust: bool) -> pd.DataFrame:
    return yf.Ticker(ticker).history(
        start=start,
        end=end,
        auto_adjust=auto_adjust,
        actions=False,
    )
```

`_history` is private (leading underscore) because it is an implementation detail:
the retry-wrapped raw yfinance call. External callers use `fetch_prices`, not this.

`@retry_on_failure` is applied here, not on `fetch_prices`. The reason is scope:
only the yfinance network call can fail from network issues. The merge and
transformation logic in `fetch_prices` cannot fail because of a rate limit or
dropped connection. Retrying `fetch_prices` would retry the merge too, which is
unnecessary and could mask a real bug in the transform logic (if the merge raises,
it should propagate immediately, not be retried three times).

`actions=False` prevents yfinance from appending dividend and split rows into the
price DataFrame. yfinance, by default, adds action rows with zero prices on
action dates, which pollutes the OHLCV data. Splits and dividends are fetched
separately in `corporate_actions.py`, where they are handled correctly.

```python
def fetch_prices(ticker: str, start: date, end: date | None = None) -> pd.DataFrame:
    try:
        raw = _history(ticker, start, end, auto_adjust=False)
        adj = _history(ticker, start, end, auto_adjust=True)
    except Exception as exc:
        raise FetchError(f"{ticker}: {exc}") from exc
```

Two separate yfinance calls. yfinance does not return both raw and adjusted prices
in a single call — you must choose one or the other via `auto_adjust`. The
alternative — computing adjusted prices from the raw prices and the splits/dividends
history — would mean implementing the exact adjustment formula ourselves, which is
error-prone and what yfinance already does correctly. Two calls is the right
tradeoff.

`raise FetchError(f"{ticker}: {exc}") from exc` — `from exc` is Python's exception
chaining syntax. It sets the new exception's `__cause__` to the original exception,
so the original traceback is visible when the `FetchError` is logged. Without
`from exc`, the original error is lost and debugging a fetch failure requires
guessing what yfinance raised.

```python
    raw.index = raw.index.normalize().tz_localize(None)
    adj.index = adj.index.normalize().tz_localize(None)
```

yfinance returns timestamps with a UTC timezone tag
(e.g., `2024-01-02 00:00:00+0000`). Postgres's `Date` column type expects a plain
date with no timezone. `.normalize()` rounds each timestamp to midnight (all daily
bars should already be at midnight, but yfinance is inconsistent). `.tz_localize(None)`
strips the timezone label, converting from timezone-aware to timezone-naive, which
pandas can then write to a `Date` column without a type mismatch error.

If this step were skipped, SQLAlchemy would raise a `DataError` or silently coerce
the timezone-aware timestamp to the wrong date in some locales — any timezone west
of UTC would shift a midnight UTC timestamp to the previous calendar day.

```python
    merged = pd.DataFrame({
        "raw_open":   raw["Open"],
        ...
    }).dropna().sort_index()
```

Building a new DataFrame from individual Series (one column at a time) rather than
renaming columns on the raw DataFrames. This is explicit about which column goes
where: if yfinance changes a column name in a future version, the `KeyError` is
raised here with a clear message, rather than propagating silently through renamed
columns. `.dropna()` on the merged DataFrame drops any row where a value from
either the raw or the adjusted fetch is missing — this is the implicit inner join.
A date present in one fetch but absent in the other gets NaN for the missing columns
and is dropped entirely, rather than stored with half-null data that would corrupt
a backtest.

```python
    merged["fetched_at"] = datetime.now(tz=timezone.utc)
```

`timezone.utc` is used explicitly rather than `datetime.utcnow()`. `utcnow()` is
deprecated in Python 3.12+ and, more importantly, returns a naive datetime that
looks like a local time. A naive datetime from `utcnow()` compared against a
timezone-aware timestamp elsewhere will raise or produce wrong results.
`datetime.now(tz=timezone.utc)` returns an aware UTC datetime, which is
unambiguous.

---

### `corporate_actions.py`

```python
        if row.get("Stock Splits", 0) != 0:
            records.append({...
                "value": Decimal(str(row["Stock Splits"])),
            })
```

The zero check excludes yfinance's padding rows. `Ticker.actions` returns a
DataFrame where every row has both a `"Stock Splits"` column and a `"Dividends"`
column. On a dividend date, the split column is 0; on a split date, the dividend
column is 0. If these zeros were not excluded, every dividend date would also log
a spurious split event with value 0 — corrupting the `corporate_actions_log` and
causing the ingest layer to incorrectly trigger a price re-fetch for non-events.

`Decimal(str(row["Stock Splits"]))` rather than `Decimal(row["Stock Splits"])` —
`row["Stock Splits"]` is a Python float (e.g., `4.0`). `Decimal(4.0)` produces
`Decimal('4')` for whole numbers, but `Decimal(0.1)` produces
`Decimal('0.1000000000000000055511...')` — the binary floating-point approximation
leaking into the supposedly exact decimal. Converting to string first
(`str(4.0)` → `"4.0"`) forces the decimal representation before Decimal parses it.

---

## 3. Design decisions and rejected alternatives

### Retry at the private function level, not the public function level

The retry decorator is applied to `_history`, `_fetch_info`, and `_fetch_actions`
— the raw yfinance calls — not to `fetch_prices`, `fetch_metadata`, or
`fetch_corporate_actions`.

The alternative — applying `@retry_on_failure` directly to `fetch_prices` — was
rejected because `fetch_prices` contains merge and transform logic after the
yfinance calls. That logic cannot fail because of a network problem. If it raises
(due to a bug, an unexpected DataFrame shape, a column-name change in yfinance),
retrying the whole function three times hides the real error for 80 seconds (two
exponential waits) before finally re-raising. The error should surface immediately.
By isolating the retry to only the network call, the merge logic either succeeds
or fails on the first attempt — no waiting, no confusion about what failed.

The cost of this decision: the split between private retry-wrapped and public
conversion functions adds a layer. Reversing it is easy — move `@retry_on_failure`
to the public function — but the debugging cost goes up.

### Two yfinance calls for raw and adjusted prices

yfinance's `history()` returns either raw or adjusted OHLCV per call, controlled
by `auto_adjust`. There is no single call that returns both. The alternatives were:

1. Store only adjusted prices (one call). Rejected because raw prices are needed
   to detect and audit corporate action effects (see
   [step-01-database-schema.md](step-01-database-schema.md)).

2. Compute adjusted prices from raw prices and the actions history. Rejected
   because this requires reimplementing yfinance's adjustment formula, which
   handles not just splits but dividend-adjusted total-return prices — a
   non-trivial calculation that yfinance already does correctly.

The cost: two network calls per ticker per ingest run doubles the yfinance traffic.
At 20 tickers this is 40 calls instead of 20 — negligible at the Stage 1 scale.
At the full S&P 500, this is 1000 calls instead of 500, which may approach
yfinance's rate limits faster; that would be addressed by adding a delay between
tickers, not by changing the two-call design.

### `FetchError` as a typed exception

The alternative — letting the raw yfinance exception propagate — was rejected
because the ingest layer's per-ticker catch block would need to catch `Exception`
to handle all possible yfinance failures. Catching `Exception` is dangerous: it
masks programming errors like `AttributeError`, `TypeError`, or `NameError` that
should crash the program and be fixed, not logged as a fetch failure and silently
skipped. `FetchError` is the declared contract: "this specific thing means yfinance
failed." Everything else propagates.

---

## 4. Concepts introduced

### Adjusted vs raw prices (reviewed from schema step)

The fetcher is the first place where the distinction becomes concrete. yfinance's
`auto_adjust=True` returns the split-and-dividend-adjusted prices; `auto_adjust=False`
returns the raw prices as they actually traded. Both are needed and cannot be
derived from each other without tracking the full history of corporate actions.
For detail on why both are stored, see [step-01-database-schema.md](step-01-database-schema.md).

### Exponential backoff

A retry strategy where each successive wait is longer than the last. Here: 4
seconds, then 16 seconds (multiplied by ~4 each time, bounded at 60). The purpose
is to avoid overwhelming a rate-limited API with rapid retries. A flat `sleep(2)`
retry fires at a predictable 2-second cadence; if the API is rate-limiting at that
cadence, every retry will also be rate-limited. Exponential spacing gives the
rate-limit window time to reset before the next attempt. The tradeoff is slower
recovery: a transient failure on the first attempt means waiting at least 4 seconds
before the retry, even if the API recovered in 0.5 seconds.

### Timezone-aware vs timezone-naive datetimes

Python has two kinds of datetime objects. A **naive** datetime has no timezone
information — it's just year, month, day, hour, minute, second, with no
interpretation. A **timezone-aware** datetime knows which timezone it represents.
yfinance returns timezone-aware timestamps in UTC; Postgres's `Date` column expects
a plain date with no timezone. When you pass a timezone-aware timestamp to a `Date`
column, the database driver must convert it to a date in some timezone — and if
that timezone is not explicitly UTC, the result depends on the system clock's local
timezone, producing different dates depending on where the server runs. Stripping
the timezone explicitly with `.tz_localize(None)` after `.normalize()` makes the
conversion deterministic regardless of server location.

---

## 5. How this component was verified

Smoke test run immediately after all four files were written:

```python
from datetime import date
from data_pipeline.fetch.prices import fetch_prices
df = fetch_prices('AAPL', start=date(2024, 1, 2), end=date(2024, 1, 4))
print(df.columns.tolist())
print(df)
```

Output confirmed: 11-column DataFrame (`raw_open` through `raw_volume`, `adj_open`
through `adj_volume`, `fetched_at`), two rows (2024-01-02 and 2024-01-03), and a
UTC-aware `fetched_at` timestamp.

**What this proves:** yfinance is reachable, the two-call merge produces the correct
column shape, the timezone stripping works, and the public API signature is correct.

**What this does NOT prove:**
- That retry logic fires correctly (the happy path never retries). The retry
  behaviour is tested in `tests/data_pipeline/test_retry.py` by patching yfinance
  to raise on demand.
- That `FetchError` is raised on exhaustion (requires the patch test).
- That `fetch_corporate_actions` correctly excludes zero-padding rows (requires
  a test with known fixture data, which `test_corporate_actions.py` provides).
- That the raw close values are numerically correct (requires comparison against an
  independent source in `tests/data_pipeline/fixtures/known_prices.csv`).

---

## 6. Interview defense

**"Why use yfinance at all? It's unofficial and unstable."**

For a research agent running backtests on historical daily bars, yfinance is the
only freely available source with reasonable depth and breadth. Bloomberg, Refinitiv,
and CRSP are $10k+/year. Quandl's free tier is narrow. Alpha Vantage has severe
rate limits. yfinance's unofficial status is a real constraint, which is why the
architecture makes the database — not yfinance — the source of truth for all
downstream components. The agent never calls yfinance; only the scheduled ingest
job does. yfinance failing at 2am means the nightly ingest is incomplete; it does
not break any running study. The retry logic and `ingestion_runs` audit trail make
failures visible without crashing anything.

**"Why didn't you just store only adjusted prices? That's what you'd backtest on."**

Adjusted prices are not static. A split announced in 2025 retroactively changes
what the "January 2020 adjusted close" looks like. If you store only adjusted and
overwrite, you lose the ability to audit: did the price change because of a
legitimate corporate action, or because of a data bug? With raw prices stored
permanently alongside adjusted, you can always verify: the raw close for any date
is the ground truth, and the adjusted close should equal raw times the cumulative
adjustment factor from all subsequent splits and dividends. That computation is the
corporate action test in Stage 1.

**"What happens if yfinance changes its column names? You hardcode 'Open', 'High',
etc."**

A `KeyError` is raised immediately at the merge step in `fetch_prices`, on the
first ingest run after the change. It is loud, specific, and easy to fix — change
the column name in one place. The alternative (more defensive code that handles
multiple possible column names) would silently pick the wrong column if yfinance
introduced a new name alongside the old one. A loud failure that tells you exactly
where to look is better than silent tolerance of unexpected input. The ingest layer
logs the `FetchError` with the full traceback, so the `KeyError` origin is
preserved.

**Hard question: "Your retry logic retries on ANY exception. What if yfinance
returns empty data silently instead of raising? Your retries won't fire."**

This is a real limitation. yfinance sometimes returns an empty DataFrame for a
valid ticker on a valid date range without raising any exception. The current code
handles this: `fetch_prices` returns an empty DataFrame when either fetch is empty
(`if raw.empty or adj.empty: return pd.DataFrame()`), and the ingest layer treats
an empty DataFrame as zero new rows — it still writes an `ingestion_run_tickers`
row with `rows_written=0` and `status='success'`. This is not ideal: a silently
empty fetch looks the same as "no new bars today," which is correct for an
incremental run but wrong for a full backfill. The honest fix is to add a
minimum-rows check in `fetch_prices` (if a full history fetch for AAPL returns
zero rows, that is almost certainly a failure), but that check requires knowing
whether this is a full or incremental fetch — complexity deferred to a later pass.
The limitation is documented and the behaviour is safe; it produces an empty cache
for that ticker, which the data-matches-known-source gate test will catch.

---

## 7. What comes next and why

The next component is the ingest layer (`src/data_pipeline/ingest/`), which takes
what the fetcher returns and writes it to the database.

The fetcher produces:
- A DataFrame from `fetch_prices` → written to `price_bars` by `ingest/upsert.py`
- A dict from `fetch_metadata` → written to `ticker_metadata` by `ingest/upsert.py`
- A list of dicts from `fetch_corporate_actions` → compared against
  `corporate_actions_log`, triggering price re-fetches for new actions

If the fetcher is wrong — wrong column names, wrong timezone handling, wrong
Decimal conversion — the ingest layer will write bad data to the database silently.
The data-matches-known-source gate test (`tests/data_pipeline/test_fetch.py`
comparing AAPL's 2024-01-02 raw close against a hand-verified value) is the check
that catches this class of error before Stage 2 reads from the database.
