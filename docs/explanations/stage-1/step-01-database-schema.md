# Step 1 — Database Schema

## 1. What this does

This component defines the five Postgres tables that form the persistent foundation
of the entire project. It is not data ingestion — no data is fetched here. It is
not business logic — no strategies or hypotheses exist yet. It is the schema: the
structure every other component will read from and write to.

Three files make up this component:

- `src/data_pipeline/db/models.py` — the five SQLAlchemy model classes, one per
  table
- `src/data_pipeline/db/session.py` — the engine (connection pool) and session
  factory
- `src/data_pipeline/db/init_db.py` — the `create_schema()` function that
  materialises the tables in Postgres

**What this is NOT for:** the schema does not enforce data quality rules (those
live in the fetcher and ingest layers), does not contain any query logic (that
lives in ingest and the backtester), and does not manage migrations (Alembic is
deferred — see Section 3).

---

## 2. Every meaningful line explained

### `models.py`

```python
class Base(DeclarativeBase):
    pass
```

`DeclarativeBase` is SQLAlchemy 2.0's way of creating a model registry. Every
class that inherits from `Base` registers its table definition into
`Base.metadata`. When `create_all()` is called later, SQLAlchemy walks that
registry and issues `CREATE TABLE` statements. The `pass` is not laziness — `Base`
genuinely has no columns of its own; its only job is to be inherited from.

Why `DeclarativeBase` and not the older `declarative_base()` function? The
function-based form is SQLAlchemy 1.x style and is now legacy. The class-based
form introduced in 2.0 supports typed annotations, which means type checkers can
catch bugs like assigning a string to an integer column before the code runs.

---

```python
class PriceBar(Base):
    __tablename__ = "price_bars"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
```

`__tablename__` is the literal Postgres table name. SQLAlchemy does not infer it
from the class name.

`Mapped[str]` is the 2.0 annotation style. It does two things: it tells the Python
type checker that `bar.ticker` is a string (not `Any`), and it tells SQLAlchemy to
treat this as a mapped column rather than a plain class attribute.

The composite primary key on `(ticker, date)` is the decision worth understanding.
A primary key in a relational database is a uniqueness constraint plus an automatic
index. Both matter here. The uniqueness constraint prevents the same ticker/date
pair from being inserted twice, which is exactly the duplicate-prevention the
incremental ingest logic depends on. The index means a query like "give me all
AAPL bars between 2020-01-01 and 2023-12-31" uses the index rather than scanning
the entire table. No separate `CREATE INDEX` statement is needed.

---

```python
    raw_open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_volume: Mapped[int] = mapped_column(BigInteger)

    adj_open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_volume: Mapped[int] = mapped_column(BigInteger)
```

Ten price columns instead of five is the central design decision of this table.
See Section 3 for the full justification.

`Numeric(18, 6)` means eighteen total digits, six after the decimal point. This is
a fixed-precision type, also called DECIMAL in SQL. It stores numbers exactly, the
way a calculator does. `Float` uses IEEE 754 binary floating point, which cannot
represent most decimal fractions exactly — 0.1 in binary is an infinitely
repeating fraction, stored as approximately 0.10000000000000001. For prices, this
matters: a series of multiplications (computing returns, applying a split ratio)
accumulates rounding error, and two computations of the "same" value may differ in
the 15th significant digit. `Numeric` has no such error. The cost is slightly
slower arithmetic, which is irrelevant at the scale of daily bars.

`BigInteger` (64-bit integer) for volume because Postgres's default `Integer` is
32-bit, capping at about 2.1 billion. On a high-volume day for a heavily-traded
stock, daily volume can reach tens of billions of shares, which overflows a 32-bit
integer silently — storing the wrong number without any error.

```python
    fetched_at: Mapped[datetime] = mapped_column()
```

Every row records when it was fetched from yfinance. This is what makes studies
reproducible: if a study gives different numbers in June than it did in February,
`fetched_at` tells you whether the data itself changed (a corporate action
triggered a re-fetch) or whether something in the analysis logic changed.

---

```python
class TickerMetadata(Base):
    __tablename__ = "ticker_metadata"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    listing_status: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column()
```

`str | None` marks a column as nullable. yfinance does not always return sector
and industry — ETFs typically have no sector classification, and some equities
have incomplete metadata. Marking these nullable means a missing sector is
stored as NULL, not as an ingest error. If these columns were non-nullable and we
required them, any ticker with incomplete metadata would fail ingest entirely,
losing all its price data too.

This is separate from `price_bars` because the refresh cadence is different. Price
bars are updated daily. Metadata is updated monthly. Mixing them would mean
re-fetching sector data every day, which is wasteful, or never updating it, which
would miss delistings.

---

```python
class CorporateActionLog(Base):
    __tablename__ = "corporate_actions_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    action_type: Mapped[str] = mapped_column(String(16))
    action_date: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    detected_at: Mapped[datetime] = mapped_column()
```

This table is append-only by convention — the ingest code never deletes from it.
Why? Because it is an audit trail. If a study produced a certain result on a
certain date, and you need to understand why that result was different from what
it would be today, you look at this table to find every adjusted-price change
that occurred between those two dates. Deleting rows destroys that ability.

`index=True` on `ticker` creates a secondary index. The primary key is `id`
(auto-incrementing integer), so queries like "what actions have occurred for AAPL?"
would scan the whole table without a separate index. Secondary index on `ticker`
makes that query fast.

An integer auto-increment PK is used here rather than a composite natural key
because there is no natural unique key per action: a ticker can receive multiple
dividends per year, and the combination of `(ticker, action_type, action_date)` is
nearly unique but not guaranteed to be (a stock could theoretically have a split
and a dividend announced on the same date). An integer surrogate PK avoids the
question entirely.

---

```python
class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    started_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16))

    tickers: Mapped[list["IngestionRunTicker"]] = relationship(back_populates="run")
```

UUID string PK rather than auto-increment integer. An auto-increment integer is
assigned by the database at insert time, which means you don't know the run's ID
before it is inserted. A UUID is generated in Python before any database call,
which means the ID can be passed to logging, included in error messages, and
referenced in child rows — all before the first `INSERT` completes. This makes
debugging partial failures easier.

`finished_at` is nullable because it does not exist yet when the run starts. The
row is inserted at `started_at` with `finished_at=None`, and updated once the
run completes. If the process crashes mid-run, `finished_at` remains NULL, which
is itself diagnostic: a run with `finished_at IS NULL` is a crashed or still-running
run, distinguishable from one with `status='failed'` (which completed but failed).

The `relationship` is SQLAlchemy's ORM join declaration. It lets you write
`run.tickers` to get all child `IngestionRunTicker` rows without writing SQL. It
does not create a database constraint — the foreign key in `IngestionRunTicker`
does that.

---

```python
class IngestionRunTicker(Base):
    __tablename__ = "ingestion_run_tickers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    rows_written: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["IngestionRun"] = relationship(back_populates="tickers")
```

`ForeignKey("ingestion_runs.id")` creates a database-level referential integrity
constraint: Postgres will refuse to insert a row here with a `run_id` that doesn't
exist in `ingestion_runs.id`. This prevents orphaned records from corrupted runs.

`error` uses `Text` rather than `String(N)`. `String(N)` truncates at N characters.
An exception traceback can be hundreds of characters. Truncating an error message —
the thing you need most after a failure — is the wrong tradeoff. `Text` in Postgres
is unbounded.

`rows_written` defaults to 0 at the schema level. A failed ticker still has a valid
row with `rows_written=0`, meaning a query for "how many total bars were written in
this run" sums correctly even for failed tickers (they contribute 0, not NULL, which
would cause the SUM to ignore them).

### `session.py`

```python
def get_engine(url: str | None = None):
    return create_engine(url or settings.database_url)

_engine = get_engine()
SessionFactory = sessionmaker(bind=_engine)
```

`get_engine` takes an optional URL so the test suite can call
`get_engine(settings.database_url_test)` to get a test-database engine, without
the function needing to know about the test database internally. The default falls
through to `settings.database_url`.

`_engine` is created once at module import time. Creating an engine is the
expensive step — it opens and pools TCP connections to Postgres. Creating it once
and reusing it is a basic performance requirement; creating one per query would be
catastrophically slow.

`SessionFactory` is a callable. `SessionFactory()` returns a new `Session` each
time it is called. Sessions are lightweight and short-lived — one per operation,
discarded when done.

### `init_db.py`

```python
def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
```

`create_schema` takes an `Engine` parameter rather than calling `get_engine()`
internally. This is the dependency injection principle at its simplest: the
function does not decide which database to connect to; the caller passes in an
already-configured engine. This is what makes it possible to write
`create_schema(get_engine(settings.database_url_test))` in the test fixture
without any config patching.

`create_all` issues `CREATE TABLE IF NOT EXISTS` for every table registered under
`Base.metadata`. It is idempotent — running it twice is safe.

---

## 3. Design decisions and rejected alternatives

### Storing both raw and adjusted OHLCV

The chosen approach stores ten price columns: five raw (`raw_open`, `raw_high`,
`raw_low`, `raw_close`, `raw_volume`) and five adjusted (`adj_*`). Raw values are
written once at fetch time and never overwritten. Adjusted values are overwritten
when a corporate action is detected.

The alternative — storing only adjusted prices and overwriting them — was rejected
for two reasons. First, it destroys the audit trail: if a study produced Sharpe
ratio X last month and now produces X/2, there is no way to determine whether the
change is due to a split retroactively rescaling prices (expected, not a bug) or
due to a data corruption (a bug). With raw prices intact, you can compute what
the adjusted prices *should* be and compare. Second, it makes the corporate-action
test impossible to write correctly: the test needs to seed "pre-split adjusted
prices" and then assert they were updated — but if raw and adjusted are the same
column, seeding the pre-split state requires fabricating data that looks wrong.

A second alternative — storing only raw prices and computing adjusted prices on
the fly — avoids the storage cost but adds computation overhead on every query.
More importantly, it requires tracking the full history of splits and dividends to
compute the adjustment factor, which is essentially rebuilding the corporate
actions log into the query layer. It's more complex, not less.

### `Numeric(18, 6)` over `Float`

`Float` is IEEE 754 binary floating point. It cannot represent most decimal
fractions exactly. The value 0.1 is stored as approximately
0.10000000000000000555. A single stored price is fine — the error is at the 17th
significant digit and you're comparing it to a human-readable number. But a
backtest compounds operations: multiply a price by a quantity, subtract a fee,
compute a percentage return, annualise it. Each multiplication multiplies the
error too. After enough operations, two computations of the "same" backtest on the
same data may return different numbers, which violates the determinism requirement.
`Numeric` stores exactly the decimal value given, at the cost of slightly slower
arithmetic. For daily bars, this is not a performance concern.

### Composite PK over a surrogate integer PK on `price_bars`

A surrogate integer PK (an auto-incrementing ID column) is a valid alternative but
was rejected because the natural composite key `(ticker, date)` is what the data
actually means, and using it as the PK achieves three things simultaneously: a
uniqueness constraint (no duplicate bars), an index for range queries (Postgres
indexes the PK), and a clear identity for upsert logic (`INSERT ... ON CONFLICT
(ticker, date) DO UPDATE`). Adding a surrogate PK alongside the composite would
have meant adding an extra `UNIQUE` constraint and a separate index anyway,
achieving nothing over the composite PK while adding complexity.

### `ingestion_run_tickers` as a separate table

The alternative is to store per-ticker outcomes in an array column on
`ingestion_runs` — a single JSON blob or a Postgres array. This was rejected
because array columns cannot be queried with standard SQL. "Which tickers failed
in the last 10 runs?" is a three-second SQL query against a normalised table and
a messy parsing exercise against a JSON blob. The normalised table also allows
foreign key constraints and indexes on `ticker`, which a JSON blob does not.

### `create_all()` over Alembic

Alembic tracks every schema change as a versioned migration file. Its value is
allowing the schema to evolve without dropping and recreating tables, because
dropping tables loses data.

During Stage 1, the only data in the database is a yfinance cache. It is
completely re-fetchable. There is no data worth protecting. Setting up Alembic
now — writing `alembic init`, configuring `env.py`, generating a first revision —
adds real tooling overhead to protect something disposable. The schema is also
still being iterated on within this stage; every design change would require a new
migration revision or a `--autogenerate` that then needs to be reviewed.

Alembic will be introduced at the Stage 1-to-Stage-2 boundary, when the backtester
first depends on a populated cache. At that point, one `alembic revision
--autogenerate` against the then-current tables becomes the baseline migration
revision, and every change from Stage 2 onward adds a new revision on top of it.
Nothing is lost by waiting.

---

## 4. Concepts introduced

### Adjusted versus raw prices

When a company does a stock split — say a 4-for-1 split — every shareholder gets
four shares for each one they held, and the price drops by a factor of four (e.g.,
$400 → $100). Nothing happened to the company's value; the same total is now
spread across four times as many shares.

The problem for historical analysis is that the price chart now shows a cliff: the
price on split day appears to drop 75% overnight. Any strategy that looks at price
changes would see a massive "sell signal" on that day, which is not a real market
event — it is an artefact of the split mechanics.

Adjusted prices fix this retroactively: every historical price before the split is
divided by four, so the chart looks like a smooth continuation. This is the
"adjusted close" that data providers like Yahoo Finance return by default.

The catch is that every new split retroactively changes all historical adjusted
prices. A split on 2024-01-15 changes what the "2020-01-01 adjusted close" looks
like. This is why raw prices exist: the 2020-01-01 raw close (what actually traded
that day) never changes. Raw is the ground truth; adjusted is a derived view that
changes with new corporate actions.

### Referential integrity

A foreign key constraint (`ForeignKey("ingestion_runs.id")` in
`IngestionRunTicker`) tells the database to refuse any insert of a
`ingestion_run_tickers` row whose `run_id` does not match an existing row in
`ingestion_runs`. This is the database enforcing a rule that application code
could also enforce — but the database is the last line of defence. Application
code has bugs; the database constraint fires regardless.

---

## 5. How this component was verified

The schema was verified by running:

```
python -m data_pipeline.db.init_db
psql strategy_research -c "\dt"
```

The `\dt` output showed all five tables: `price_bars`, `ticker_metadata`,
`corporate_actions_log`, `ingestion_runs`, `ingestion_run_tickers`.

**What this proves:** SQLAlchemy's model definitions are valid Python, the
connection string in `.env` is correct, and Postgres accepted all five `CREATE
TABLE` statements without constraint or type errors.

**What this does NOT prove:** that the column types are correct for the data that
will actually be ingested (that requires seeing real yfinance output), that the
composite PK on `price_bars` prevents duplicates as intended (that requires an
upsert test), or that the `ingestion_run_tickers` foreign key enforces correctly
(that requires an integration test). Those validations come when the ingest layer
is built and tested.

---

## 6. Interview defense

**"Why store both raw and adjusted prices instead of just adjusted?"**

Raw prices are permanent. Adjusted prices change every time a split or dividend is
announced, because the adjustment is applied retroactively to all history. If you
store only adjusted and overwrite on a corporate action, you lose the ability to
distinguish a legitimate data update from a data corruption — the before and after
are both just "the current adjusted price." Storing raw preserves the ground truth;
adjusted is then a derived view layered on top of it. The test that validates the
corporate-action handler works precisely because the raw values are untouched after
the adjusted values are rewritten.

**"Why not use Float for prices? Isn't it fast enough?"**

Speed is not the concern — correctness is. IEEE 754 binary floating point cannot
represent most decimal fractions exactly. For a single stored price, the error is
negligible. For a backtester computing compounded returns over years, applying
split ratios, and then annualising results, each operation multiplies the
accumulated rounding error. Two runs of the same backtest on the same data can
return different results at the 10th or 12th significant digit. That violates the
determinism requirement — the backtester must be a pure function, same inputs to
same outputs. `Numeric` is exact, slower at scale, and irrelevant at the scale of
daily bars for a few dozen tickers.

**"This is a cache of disposable data. Why bother with proper schema design at all
— why not just dump CSVs?"**

The reproducibility requirement. The same study run twice must use identical data.
Files are harder to query, harder to enforce uniqueness on, harder to update
atomically (the corporate actions handler overwrites only the `adj_*` columns for
affected rows — you cannot do that in a CSV without rewriting the whole file and
risking partial-write corruption). The audit trail in `corporate_actions_log` is
impossible in flat files without a separate ledger file you have to keep
synchronised manually. The per-ticker failure isolation in `ingestion_run_tickers`
is a table join; in files it would require naming conventions and directory
structure you enforce by hand. Every one of these problems has a trivial solution
in Postgres and a messy custom solution in files.

**"Your Alembic decision will cost you — you'll have to drop and recreate the
database the moment you need a schema change during Stage 1. Is that acceptable?"**

Yes, explicitly. The data in the Stage 1 database is a yfinance cache.
Re-fetching it takes minutes. There is no irreplaceable data to protect. Alembic's
value is protecting data that would be expensive to lose — hypotheses, verdicts,
scoreboard entries — none of which exist before Stage 4. The honest cost of this
decision is that a schema change during Stage 1 development requires dropping and
recreating tables and re-running the ingest. That is an acceptable 10-minute
inconvenience. The alternative cost — setting up and maintaining migrations to
protect a throwaway cache — is a real engineering overhead that would slow down
Stage 1 iteration for no benefit.

---

## 7. What comes next and why

The next component is the session factory and database connection utilities
(already completed as `session.py` — see the code), followed immediately by the
yfinance fetcher layer (`src/data_pipeline/fetch/`).

The fetcher is what puts data into the tables defined here. It produces the raw and
adjusted prices that populate `price_bars`, the sector/industry data that populates
`ticker_metadata`, and the splits/dividends data that feeds `corporate_actions_log`.

If the schema were wrong — wrong column types, wrong composite key, missing tables
— the fetcher would either crash on write or silently store bad data. The backtester
(Stage 2) reads from `price_bars` and performs arithmetic on the price columns. If
those columns were `Float` instead of `Numeric`, the backtester would be computing
on subtly incorrect values, producing results that look plausible but are not
deterministic. The schema is load-bearing for every downstream stage.
