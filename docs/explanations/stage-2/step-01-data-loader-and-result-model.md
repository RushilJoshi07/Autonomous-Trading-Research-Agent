# Step 1 — Data Loader and Result Model (Stage 2)

## 1. What this does

Before these two files existed, the backtesting library had no connection to the
database or to the rest of the project. After they exist:

- `load_price_data` is the bridge from the Postgres cache (built in Stage 1) to
  backtesting.py. It reads a ticker's price history and hands back a DataFrame in
  exactly the shape backtesting.py requires.
- `BacktestResult` is the typed output contract for every backtest run. A caller
  receives a structured object with named, validated fields — not a raw pandas
  Series full of string keys that are easy to misread or mistype.

What this is NOT: neither file runs a strategy or makes any research claim. The
data loader is a pure read function. The result model is a data container. All the
interesting logic lives in the engine (Step 2) and the strategies (Step 2).

---

## 2. Every meaningful line explained

### load_price_data

```python
def load_price_data(
    ticker: str,
    session: Session,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
```

`session: Session` is the first design decision visible in the signature. The
function accepts an open SQLAlchemy session rather than creating one itself. This
is explained fully in section 3, but the short version is: the caller controls the
session lifecycle, which makes testing straightforward and avoids hidden
module-level state.

`start` and `end` are both `None` by default. If omitted, the query returns the
full history for the ticker. This is intentional — a caller running a full-history
backtest should not have to know (or hardcode) the earliest date in the cache.

---

```python
stmt = select(PriceBar).where(PriceBar.ticker == ticker)
if start:
    stmt = stmt.where(PriceBar.date >= start)
if end:
    stmt = stmt.where(PriceBar.date <= end)
stmt = stmt.order_by(PriceBar.date)
```

The query is built incrementally: the base query filters by ticker, and the date
conditions are added only if provided. The `order_by(PriceBar.date)` at the end is
not optional — backtesting.py assumes the DataFrame index is monotonically
increasing. If rows arrive in random order (which Postgres does not guarantee
without ORDER BY), the backtester would silently produce wrong results by
processing bars out of sequence.

The alternative — always passing `start` and `end` as required arguments — was
rejected because it would force every caller to look up the earliest cached date
before calling this function. That lookup is already a query; making it mandatory
for every call adds needless ceremony.

---

```python
rows = session.execute(stmt).scalars().all()
if not rows:
    raise ValueError(f"No price data found for {ticker} ({start} – {end})")
```

`.scalars().all()` materialises the query into a Python list of `PriceBar` objects.
This pulls all rows into memory at once. For daily bars over a few years (roughly
2,500 rows per ticker), this is fine. If this function were ever adapted for
intraday data at scale, streaming via `.yield_per()` would be needed instead.

`raise ValueError` when no rows are found is more informative than returning an
empty DataFrame. An empty DataFrame passed to backtesting.py causes a confusing
internal error deep inside the library with no message about why. A ValueError at
the boundary — with the ticker name and date range — gives the caller the exact
information needed to diagnose the problem (wrong ticker, data not yet ingested,
date range outside the cache).

---

```python
df = pd.DataFrame(
    {
        "Open":   [float(r.adj_open)  for r in rows],
        "High":   [float(r.adj_high)  for r in rows],
        "Low":    [float(r.adj_low)   for r in rows],
        "Close":  [float(r.adj_close) for r in rows],
        "Volume": [int(r.adj_volume)  for r in rows],
    },
    index=pd.DatetimeIndex([pd.Timestamp(r.date) for r in rows]),
)
```

Five things happening here at once, each for a reason:

**`adj_*` not `raw_*`** — adjusted prices account for splits and dividends, so
the series is continuous across corporate actions. Raw prices show a fake 75%
drop on the AAPL split date of 2020-08-31. A strategy backtested on raw prices
would generate a phantom short on that drop and a phantom long on the "recovery"
— trades that never happened in reality. Adjusted prices prevent this. The deeper
explanation of why both raw and adjusted are stored at all is in
[step-01-database-schema.md](../stage-1/step-01-database-schema.md).

**`float(r.adj_open)` etc.** — the database stores prices as `Numeric(18,6)`,
which SQLAlchemy returns as Python `Decimal`. backtesting.py expects ordinary
`float`. Wrapping in `float()` converts. Without this, backtesting.py raises a
TypeError when it tries to do arithmetic on Decimal values.

**`int(r.adj_volume)`** — volume is stored as `BigInteger`, which SQLAlchemy
returns as a Python `int`, but the explicit cast makes the contract clear.
backtesting.py's volume handling is tolerant of numeric types, but being explicit
prevents surprises if the ORM ever returns a different numeric type.

**`pd.DatetimeIndex([pd.Timestamp(r.date) for r in rows])`** — `r.date` is a
Python `datetime.date` object (no time component). backtesting.py requires a
`DatetimeIndex`, not a `DateIndex`. `pd.Timestamp(r.date)` converts each date to
midnight UTC, giving the DatetimeIndex backtesting.py expects. Passing a raw
`DateIndex` causes a TypeError inside backtesting.py's internal data handling.

**Column names are capitalised** — backtesting.py accesses columns by exact name:
`data.Open`, `data.Close`, etc. If the column were named `open` or `adj_close`,
backtesting.py would raise an AttributeError when trying to access the price data.
The capitalisation is not style — it is a contract with the library.

---

```python
df.index.name = "Date"
```

backtesting.py checks `df.index.name == "Date"` (capital D) in some code paths.
If the index is unnamed or named differently, certain internal operations fail. One
line, non-negotiable.

---

### BacktestResult

```python
class BacktestResult(BaseModel):
    ticker: str
    start: date
    end: date
    sharpe_ratio: float
    max_drawdown_pct: float
    annual_return_pct: float
    total_return_pct: float
    num_trades: int
    win_rate_pct: float
    commission_pct: float
```

Every field is typed. Pydantic validates on construction — if backtesting.py
ever returns a stat in an unexpected format, the model raises a `ValidationError`
at parse time rather than silently passing garbage forward into a verdict.

`commission_pct` records what costs were applied, not just what the return was.
The reason is the audit trail: when the agent in Stage 5 writes a verdict claiming
"Sharpe 1.4 with 0.1% commission," that claim can be checked against the
`commission_pct` field in the stored `BacktestResult`. If `commission_pct` is
missing from the result, the cost assumption is invisible — callers either
hardcode it elsewhere or forget it entirely.

---

```python
@classmethod
def from_stats(
    cls,
    stats: "pd.Series",
    ticker: str,
    commission: float,
) -> "BacktestResult":
```

`from_stats` is a classmethod rather than an `__init__` override because it
performs parsing logic (extracting string keys, handling NaN) that is specific to
the backtesting.py stats format. If we ever add a second backtesting library, we
would add `from_vectorbt_stats` as a separate classmethod rather than making
`__init__` handle multiple formats. The classmethod pattern makes the source of
the data explicit at the call site: `BacktestResult.from_stats(stats, ...)` is
immediately readable; `BacktestResult(stats, ...)` is not.

The `stats` type annotation is a forward reference string `"pd.Series"` to avoid
importing pandas at the module level. `result.py` has no direct use of pandas
other than accepting this one argument — importing the full library for a type
hint is wasteful. The string annotation defers the import until runtime.

---

```python
def _f(key: str) -> float:
    v = stats.get(key, float("nan"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")
```

This is the NaN tolerance layer. backtesting.py returns `NaN` for stats that are
undefined when a strategy makes zero trades. For example, `Sharpe Ratio` requires
a standard deviation of returns — if there are no trades, there are no returns,
and the Sharpe is undefined. Python's `float("nan")` is a valid IEEE 754 float
value, so Pydantic accepts it without error. The caller can check
`math.isfinite(result.sharpe_ratio)` to detect this case.

The alternative — crashing on NaN — would make zero-trade backtests fail at
result construction. A zero-trade result is useful information: it means the
strategy's entry conditions were never triggered on this data, which is a valid
finding (not a programming error). The caller needs to receive this result so they
can report it, not have it blow up.

---

```python
start=stats["Start"].date() if hasattr(stats["Start"], "date") else stats["Start"],
```

backtesting.py's `"Start"` and `"End"` stats can be `pandas.Timestamp` objects or
`datetime.date` objects depending on the version and data format. `hasattr(...,
"date")` checks whether the value is a Timestamp (which has a `.date()` method)
and calls it if so, converting to `datetime.date`. If it is already a `date`, it
passes through unchanged. Without this, a Timestamp would fail Pydantic's `date`
type validation.

---

## 3. Design decisions and rejected alternatives

### Accepting a Session vs. creating one internally

The most consequential design choice in `load_price_data` is accepting a `Session`
as an argument rather than calling `SessionFactory()` internally.

The alternative would be:

```python
def load_price_data(ticker, start=None, end=None):
    with SessionFactory() as session:
        ...
```

This pattern is used in the ingest runner, which is a long-running loop that needs
its own isolated sessions per ticker. The data loader is fundamentally different: it
is a read function called directly by a test or by the engine. Making it manage its
own session would mean tests have to monkeypatch `SessionFactory` at the module
level to redirect reads to the test database — the same machinery used for the
runner. But the runner needs that monkeypatching because it is a fire-and-forget
function that the test cannot pass a session to. The data loader can receive a
session; there is no reason to force the monkeypatching pattern here.

Accepting the session also makes the function composable: a caller that already
has an open session can pass it without creating a second connection. A function
that manages its own session cannot participate in the caller's transaction scope.

The cost of this decision: callers must manage session lifecycle themselves. For
tests this is handled by the `db_session` fixture. For production use, the MCP
tool wrapper (Stage 4) will open a session, call `load_price_data`, and close it.

### Using adj_* columns

This is documented more fully in [step-01-database-schema.md](../stage-1/step-01-database-schema.md).
The short version for this component: a backtest on raw prices would see fake
corporate-action crashes as real signals. The backtester would generate fictitious
trades on fabricated price moves. The Sharpe ratio would be meaningless. Using
adjusted prices is not optional for any strategy research that spans more than a
few months on any ticker with splits or dividends.

The question of whether to use raw prices for specific research questions (e.g.,
"what would a strategy have returned for someone who did NOT adjust for splits")
is a legitimate one, but it is not the default case. Raw prices are available in
the database if needed; adding a `use_raw=False` parameter to `load_price_data`
would expose them without changing the default.

### Raising ValueError vs. returning an empty DataFrame

Returning an empty DataFrame on "no data found" is a common pattern that seems
convenient. It is rejected here because backtesting.py does not handle empty
DataFrames gracefully — it raises an internal error with a stack trace that points
into the library internals, not at the caller's mistake. A ValueError at the
boundary, with the ticker name and date range in the message, makes the failure
immediately actionable.

---

## 4. Concepts introduced

### Adjusted vs. raw prices (cross-reference)

Covered in depth in [step-01-database-schema.md](../stage-1/step-01-database-schema.md).
The new thing introduced here is the consequence of getting it wrong at the
backtester boundary: a strategy backtested on raw prices will generate phantom
trades at split dates, producing fictitiously high Sharpe ratios that look like
real edges. The error is not caught by any assertion — the numbers are internally
consistent, just wrong.

### The audit trail pattern

`commission_pct` in `BacktestResult` is an example of a broader pattern that
will appear throughout the project: every claim must reference the data that
produced it. The agent in Stage 5 will write verdicts like "Sharpe 1.4 with 0.1%
commission." For that claim to be verifiable, the commission used must be stored
alongside the Sharpe, not reconstructed from context. This is the same principle
as scientific pre-registration: the parameters are locked at run time, not
reported after seeing the result.

---

## 5. How the verification gate was satisfied

These two components are tested in `tests/backtester/test_data_loader.py`. The
full gate (Sacred Gate 1) is closed in `test_sacred_gate.py` and described in
[step-03-sacred-gate.md](step-03-sacred-gate.md).

`test_columns_and_index` — seeds synthetic price data into the test DB, calls
`load_price_data`, and checks that the returned DataFrame has exactly the five
expected columns with a DatetimeIndex named "Date" and 500 rows. This confirms
the renaming and index construction are correct.

`test_values_match_seeded_adj_close` — iterates every row of the returned
DataFrame and compares `Close` against the `adj_close` value that was seeded.
This is the adj_* passthrough check: if the function accidentally read `raw_close`
instead, the values would differ after any synthetic split event.

`test_date_range_filter` — calls `load_price_data` with `start` and `end` and
confirms the returned rows respect the bounds. This tests the conditional WHERE
clauses.

`test_missing_ticker_raises` — calls `load_price_data` with a ticker not in the
database and confirms `ValueError` is raised with the expected message.

What these tests do NOT prove: that the adj_* values are correct for real tickers
with actual split histories. That correctness was established in Stage 1's
corporate action tests. This component's tests take correct data as given and
prove the loader reads and shapes it correctly.

---

## 6. Interview defense

**Q: Why does load_price_data accept a Session instead of managing its own?**

A: The function is called directly in tests and by the engine. If it managed its
own session internally, tests would have to monkeypatch `SessionFactory` at the
module level to redirect reads to the test database. That pattern is appropriate
for fire-and-forget functions like the ingest runner, but the data loader is a
read function that tests call directly. Accepting a session is simpler: tests pass
the test session, production code passes a session it already has open. The caller
controls the lifecycle — no hidden connection management inside the function.

**Q: Why didn't you just return the backtesting.py stats Series directly instead
of parsing it into a Pydantic model?**

A: The stats Series uses string keys like `"Sharpe Ratio"` and `"Max. Drawdown
[%]"`. A typo in any downstream code that accesses these keys fails silently with
`NaN` (via `.get()`) or raises a `KeyError`. `BacktestResult` converts those
string keys to named, typed attributes at parse time: a typo in the model raises
an error at construction, not silently later. The model also adds `commission_pct`
— which is not in the stats Series at all — as an explicit audit field. And it
normalises the NaN handling in one place rather than leaving every caller to deal
with it separately.

**Q (hard): Your Sacred Gate 1 proof uses a pre-shifted signal column to
demonstrate lookahead. But that's lookahead at the data-preparation layer. What
prevents lookahead inside a strategy's `next()` method — for example, indexing
into the data array with a positive offset?**

A: In backtesting.py, within `next()`, `self.data.Close` is a `_Array` object
that wraps the price data up to the current bar. The `[-1]` index gives the
current bar (most recent). Positive indices in this internal representation access
older bars, not future bars. Accessing future data through backtesting.py's API is
structurally impossible through `self.data`. The realistic lookahead failure is
not inside `next()` — it is at the data-preparation layer, where someone computes
an indicator using the whole DataFrame (with a `.shift(-1)` or a pandas window
that peeks at future rows) before passing it to the backtester. That is exactly
what the gate test simulates: a poisoned Signal column computed with `shift(-1)`.
The test proves that if this happens, the Sharpe is detectably inflated — which is
the detection mechanism. The structural prevention is that `load_price_data` reads
raw OHLCV columns from the database with no transformations; there is no shift
applied anywhere in the data path.

---

## 7. What comes next and why

The engine (Step 2) takes `load_price_data`'s output and a strategy class and
calls backtesting.py. It returns a `BacktestResult`. If `load_price_data` returns
wrong data — wrong columns, wrong types, wrong adj/raw — the engine will produce
a `BacktestResult` with plausible-looking numbers that are actually wrong. The
backtester cannot detect this. The data loader is the last point where data
correctness can be verified before it enters the backtest.

Stage 3 will introduce a typed strategy schema — a Pydantic model that describes
a strategy rule (indicator, parameters, entry/exit conditions) in a format the
agent can emit and the backtester can execute. `BacktestResult` will become the
return type for the backtester MCP tool in Stage 4, so its schema is effectively
locked from this point.
