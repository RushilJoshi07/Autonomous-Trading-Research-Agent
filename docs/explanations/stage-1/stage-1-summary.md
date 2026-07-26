# Stage 1 — Data Pipeline: Synthesis

**Gate result:** 6/6 tests pass. Stage 1 is complete.

Step-level code walkthroughs live in:
- [step-01-database-schema.md](step-01-database-schema.md) — models, create_all, raw/adj column design
- [step-02-fetcher.md](step-02-fetcher.md) — retry decorator, FetchError, two-call pattern, timezone handling
- [step-03-ingest-layer.md](step-03-ingest-layer.md) — upsert, incremental start, per-ticker isolation, corporate actions, survivorship bias
- [step-04-test-suite.md](step-04-test-suite.md) — golden value, caching test design, isolation proof, retry patching

This document does not re-walk the code. It covers what the individual step documents could not: how the pieces fit together, why the component boundaries sit where they do, and what the gate actually proved versus what it left unverified.

---

## 1. What Stage 1 delivered

Before Stage 1: an empty Python project with Postgres running.

After Stage 1: a populated database of raw and adjusted OHLCV prices for 17 equity
tickers and one ETF, covering 2010 to the present, with:

- A retry-wrapped yfinance client that degrades gracefully per-ticker rather than
  crashing the whole run
- Incremental caching — a second run fetches only dates not yet in the database
- Corporate action detection — splits and dividends are detected and adjusted prices
  are updated, while raw prices are protected by the upsert query itself
- A per-ticker audit trail — every run records which tickers succeeded and which
  failed, along with row counts and error messages
- A documented survivorship bias limitation — the universe is today's hand-picked
  tickers; delisted and bankrupt names are absent, and this is stated plainly rather
  than hidden

What Stage 1 is not: it is not the backtester, not the agent, and not a solution to
survivorship bias. It is the data layer that every later stage reads from.

---

## 2. How the components fit together and why the boundaries sit where they do

The pipeline has three logical layers that are deliberately separated.

**The fetch layer** (`fetch/`) talks to yfinance and nothing else. It knows how to
retry, how to parse raw versus adjusted prices, and how to raise `FetchError` when
it gives up. It does not know about the database, the cache, or the ingest run.
This separation exists so the fetch layer can be tested in isolation with a mocked
yfinance, and so a future replacement for yfinance (a paid data provider, a
different free source) touches exactly one place.

**The ingest layer** (`ingest/`) owns the database writes. It decides what needs
fetching (via `get_last_cached_date`), calls the fetch layer, and writes the result
in a per-ticker transaction. It knows about the database but not about yfinance
directly. This separation exists because the write-versus-fetch boundary is where
transactional semantics live: the ingest layer is responsible for the all-or-nothing
commit per ticker, which the fetch layer cannot be responsible for because it does
not know what is already in the database.

**The runner** (`ingest/runner.py`) owns the loop and the audit trail. It creates
the `IngestionRun` record, calls the ingest layer per ticker, catches `FetchError`,
records per-ticker results, and computes the overall run status. It is the only
place that knows about the full ticker list. This separation exists because the
audit trail and the loop logic are orthogonal to what any individual ticker does.
Making the ingest layer responsible for audit records would require it to know about
the run context, which would make per-ticker unit testing harder and mix concerns
that do not belong together.

The net result is that each layer has exactly one job and can be tested without
exercising the others.

---

## 3. Cross-component design decisions

These decisions span more than one step explainer and are not fully explained in any
of them.

### The agent reads the database, never the network

The single most important architectural constraint of Stage 1 is this: the research
agent that will be built in Stage 5 must never call yfinance. It reads the Postgres
cache exclusively.

The reason is reproducibility. A trading strategy backtest is a claim: "on this
universe, with these rules, over this period, the Sharpe ratio was X." That claim
is only meaningful if running the same backtest twice produces the same result.
yfinance is unofficial, changes its output format without notice, and is occasionally
incorrect. A backtest run on Tuesday that reads from yfinance directly can differ
from the same backtest run on Wednesday because yfinance adjusted a split factor, or
returned NaN for a ticker it previously had data for.

Reproducibility requires that the data be frozen at read time. The cache provides
this: once a bar is written to the database, it does not change except for the
controlled update of adjusted prices when a corporate action is detected, which is
itself logged and auditable.

This constraint is enforced by convention in Stage 1 — only the ingestion runner
talks to yfinance — and will be enforced structurally in Stage 4 when the market
data tool is wrapped as an MCP server that the agent calls. The MCP server will
read from the database. The agent will never import yfinance.

### Raw and adjusted prices are separate columns, not derived values

Adjusted prices change. When a 4-for-1 split happens, yfinance retroactively
adjusts all historical adjusted prices to divide by four. Raw prices do not change.
The date the stock was worth $400 is still the date it was worth $400, even if
today its adjusted price is shown as $100 to account for the later split.

Storing raw and adjusted in separate columns means the ON CONFLICT upsert can treat
them differently: the `set_` clause in the upsert includes `adj_*` columns but omits
`raw_*` columns. This is SQL-level protection, not application convention. The
application could be wrong in a dozen ways — passing the wrong column name, forgetting
to exclude raw, writing a new upsert function that does not inherit this discipline
— but the SQL is fixed. The only way to change a raw price is to explicitly write a
query that targets raw columns.

The alternative was to store only adjusted prices and derive raw by reversing the
split history. This is how some databases work and it is technically correct. It was
rejected because the reverse-derivation requires knowing the full corporate action
history at query time, which adds complexity to every read. It also means raw prices
are a computed view, not a stored fact, which makes it harder to verify that they
are correct. Storing both is redundant but makes each column directly inspectable
and the data model simpler to reason about.

A second alternative was to store only raw prices and apply adjustments at read time
in a query or view. This was also rejected: it moves corporate action logic from a
single write-time operation into every read path, making errors harder to catch and
potentially inconsistent if the adjustment logic is applied differently in different
contexts.

### Per-ticker transaction isolation was a deliberate choice, not an accident

The ingest runner opens a new SQLAlchemy session for each ticker and commits or
rolls it back independently. This is not the default for most database access
patterns, which use a single long-running session for convenience.

The choice was made because the guarantee we need is per-ticker atomicity across
the whole run: either all of a ticker's new bars for this run commit, or none of
them do, regardless of what happens to any other ticker. A single run-level
transaction would satisfy this for any individual ticker, but a failure on ticker
14 would roll back tickers 1 through 13 — which is the worst possible outcome. The
run would record no successful writes even for tickers where everything went right.

Per-row commits (committing after each price bar) were rejected because they allow
partial writes: if a failure occurs partway through a ticker's batch, some bars are
in the database and some are not. The next run would then try to re-fetch the
missing bars, but `get_last_cached_date` would see the last successfully committed
bar as the anchor, producing a gap in the data that is invisible without careful
inspection.

Per-ticker commits are the only granularity that satisfies both requirements: no
partial ticker writes, and no cross-ticker rollback contamination.

### create_all versus Alembic: deferred, for the right reason

`create_all` was chosen over Alembic for Stage 1. Alembic is a migration tool whose
value is evolving a schema without losing data that is expensive to regenerate.
Nothing in Stage 1 meets that bar. The Postgres cache is a cache — every row in it
can be re-fetched from yfinance in about ten minutes. The schema was also actively
changing during Stage 1 development as design decisions were made.

Adding Alembic now would require: running `alembic init`, writing `env.py`, running
`alembic revision --autogenerate` to create a baseline migration, adding migration
runs to the deployment process, and maintaining the migration history as the schema
changes. All of this to protect data that can be rebuilt by running `python -m
data_pipeline.cli ingest`.

Alembic will be introduced at the Stage 1→2 boundary, once the backtester depends
on a populated cache and dropping the database becomes genuinely costly. At that
point, one `alembic revision --autogenerate` against the `create_all`-produced
schema becomes the baseline migration. Nothing is lost by waiting.

### Survivorship bias: disclosed, not solved

The universe in `universe.py` is today's hand-picked tickers. Every ticker on the
list is a company that exists and trades today. Companies that went bankrupt between
2010 and the present — Lehman Brothers, several energy firms during the 2015–2016
crash, various retail names — are invisible. This inflates any backtested
performance because every ticker in the study "made it."

The correct fix is point-in-time universe reconstruction: building a record of which
tickers were in the index at every historical point, so a study of "S&P 500
constituents in 2012" actually uses the 2012 constituents and not the 2024 survivors.
This requires data that is not freely available — delisted tickers often have no
free price history, and ticker symbols get reused after delistings.

The decision was to document the bias, measure it where possible (how many of the
intended universe are missing), and disclose it in every study that uses this data.
This is the honest position, and it is more defensible than claiming a complete
solution. The alternative — claiming the universe is unbiased because it covers "a
broad range of companies" — is incorrect and would be apparent to anyone who asked
what happened to the energy sector in the data.

Stage 1 puts the disclosure in `universe.py` (lines 1–5) and in the architecture
document. The agent in Stage 5 will be required to cite this limitation in any
verdict that uses this universe.

---

## 4. Concepts that span multiple components

### Adjusted versus raw prices

Covered in depth in [step-01-database-schema.md](step-01-database-schema.md) and
[step-03-ingest-layer.md](step-03-ingest-layer.md). The synthesis: raw prices
answer "what did this stock trade for on this date?" Adjusted prices answer "what
would this stock trade for on this date if we expressed all historical prices in
terms of today's shares?" Raw is stable. Adjusted changes whenever a split or
dividend occurs. Both are necessary because a backtest needs adjusted prices to
compare performance across splits, but the raw price is what you would have actually
paid on that date, and it should never appear to change retroactively.

### Survivorship bias

Covered in [step-03-ingest-layer.md](step-03-ingest-layer.md). The synthesis: the
bias is not visible in the output. A strategy study that shows "this approach
returned X% annually on technology stocks from 2010 to 2024" looks credible until
you notice that the universe is today's technology stocks — which, by definition,
survived. The ones that went to zero are not in the data. The reported return is
real for the companies that happened to be outstanding choices in retrospect. It is
not real for any investor who was choosing from the full universe in 2010 without
knowing which ones would survive.

### Reproducibility as a correctness requirement

In a production system, you typically want the freshest data on every run.
In a research system, reproducibility is more important than freshness, because
you are making claims about the past. A backtest is an experiment. Experiments must
be repeatable. If the data changes between runs, the experiment is not repeatable,
and the claim "this strategy had a Sharpe of 1.4" becomes untestable.

The cache exists not for performance (though it helps) but for correctness. The
alternative — reading from yfinance on every backtest run — produces non-repeatable
results and undermines every research claim the agent makes.

---

## 5. What the verification gate proved and what it did not

The gate required three things: data matches a known source; caching works; failures
degrade gracefully. Each was tested.

**Data matches a known source**: `test_data_matches_known_source` confirmed that
AAPL's raw close on 2024-01-02 stored in the database is within $0.01 of 185.64, a
value verified against an independent source. This proves the pipeline does not
systematically corrupt prices — the fetch, parse, type conversion, upsert, and
retrieval chain produces a correct number for at least one known case.

What it does not prove: that all prices are correct. A single golden value cannot
detect a bug that affects only certain tickers (e.g., tickers with high split
frequency), certain date ranges (e.g., dates near market holidays), or the
adjusted-versus-raw distinction for any ticker other than AAPL. It is a smoke test,
not a comprehensive audit.

**Caching works**: `test_caching` confirmed that after a first ingest, the second
ingest's start argument is strictly later than the first's, proving `get_last_cached_date`
was read and used. What it does not prove: that the incremental window is computed
with correct off-by-one arithmetic. A bug that fetches from `last_date` instead of
`last_date + 1 day` would re-fetch the last cached bar on every run (a minor
inefficiency, not a data corruption) and would not be caught by the current test.

**Failures degrade gracefully**: `test_partial_run_failure` confirmed that DDD's
FetchError produced zero rows in `price_bars` for DDD, non-zero rows for the other
four tickers, and `partial_success` status on the run. This directly proves
per-ticker isolation. What it does not prove: that exceptions other than FetchError
are handled. A programming error (`AttributeError`, `ValueError`) would propagate
uncaught through the runner's per-ticker loop, crash the rest of the run, and leave
no error record for the remaining tickers. The design document says to only catch
`FetchError`, and that is correct — programming errors should crash. But this is a
constraint the reviewer should be aware of.

**Corporate action handling**: `test_split_detection_and_readjustment` confirmed
both that `adj_close` was updated to approximately 100.0 and that `raw_close`
remained at 400.0. This is the load-bearing assertion for the raw/adj separation.
What it does not prove: correct handling of dividends, or of multiple simultaneous
corporate actions, or of a split that arrives with a different value than expected.
The test uses a single 4:1 split as the fixture case.

**Retry and exception type**: `test_retries_on_transient_failure` confirmed three
history calls before success. `test_fetch_error_on_exhaustion` confirmed `FetchError`
is raised rather than a bare `Exception`. What they do not prove: that the
exponential backoff waits are actually applied in production (the test suppresses
the sleep function). The wait logic is correct by construction (it uses tenacity's
`wait_exponential`), but is not tested under real timing.

The residual risk from the gate: the pipeline is very likely correct for the
common cases. The corners that are not covered — other exception types, multi-ticker
corporate actions, per-date correctness beyond one golden value — represent known
gaps, not unknown unknowns.

---

## 6. Interview defense

**Q: Why store both raw and adjusted prices? Most financial databases store only
adjusted.**

A: Adjusted prices change retroactively when a split occurs. If you only store
adjusted, and a 4:1 split happens tomorrow, all of your historical adjusted prices
are now different numbers. Any backtest you ran last week with yesterday's adjusted
prices would produce a different result if you ran it again today. Reproducibility
is gone. Storing raw prices gives you a stable anchor: raw prices never change after
the trade date. The adjusted column absorbs the retroactive changes, and the upsert
is written to update adjusted columns while omitting raw columns from the ON
CONFLICT clause — SQL-level enforcement, not a convention. The corporate action test
is what closes the loop by proving raw_close is unchanged after a simulated split.

**Q: Why didn't you just use Alembic from the start?**

A: Alembic's value is evolving a schema without losing data that is expensive to
regenerate. During Stage 1, the cache is re-fetchable in ten minutes, and the schema
was actively being designed. Adding Alembic now would mean writing an env.py,
creating a baseline migration, and managing migration history — all to protect data
with no replacement cost. That is tooling overhead in service of nothing. Alembic
gets introduced at the Stage 1→2 boundary, when the backtester depends on a
populated cache and dropping the database has a real cost. At that point, one
`--autogenerate` against the existing schema gives us the baseline. Nothing is lost
by waiting.

**Q: You said reproducibility is a correctness requirement — but yfinance can change
adjusted prices any time. Doesn't the cache drift out of sync and become wrong?**

A: Yes, and this is handled intentionally. The corporate action checker runs on a
schedule (Stage 8, not yet implemented) and detects when yfinance returns new splits
or dividends not yet in the log. When it finds one, it re-fetches adjusted prices
for the affected ticker and updates `adj_*` columns — while leaving `raw_*` alone.
The log provides a full audit trail of what was changed and when. So the cache is
"frozen" in the sense that it does not silently drift — every change to adjusted
prices is an explicit, logged operation. Raw prices never change. The trade-off is
that a study run before and after a detected corporate action may show different
adjusted-price results for historical bars, which is not a bug — it is the correct
behaviour. What the cache prevents is accidental drift from yfinance returning
different numbers for unrelated reasons.

**Q (hard): Your one golden value test proves AAPL 2024-01-02 is correct. But AAPL
has not had a split since 2020. What if the split-adjustment logic in the upsert is
broken and you just can't see it because your golden value test uses a ticker with
no split history in the test window?**

A: That is a real gap. The golden value test verifies the fetch-and-store pipeline
for a stable case. The split-adjustment logic is tested separately — `test_split_detection_and_readjustment` directly exercises the adjustment code path
using seeded pre-split data. The two tests together cover what a single golden value
cannot: correctness of the basic pipeline, and correctness of the update path after
a corporate action. What neither test covers is a golden value for adjusted prices
after a real historical split — that would require a ticker with a split in the test
window and an independently-verified pre-split adjusted price. That gap exists and
the honest answer is: the tests are sufficient to catch the common failure modes but
do not constitute a complete audit of every code path.

---

## 7. What comes next and why Stage 1 must be correct for Stage 2 to work

Stage 2 builds the backtesting engine. The backtester reads `price_bars` from
Postgres and never touches the network. It has no way to know whether the data it
reads is correct. If Stage 1 is wrong, Stage 2 produces wrong results silently and
confidently — the backtester just runs whatever numbers it is given.

The specifically dangerous failure is a split event in the raw prices. If `raw_close`
for AAPL were to show a 75% drop on the split date (because the raw and adjusted
logic got swapped), the backtester would see a catastrophic single-day decline and
generate a signal — short before the drop, long after the recovery. The backtested
Sharpe ratio would be enormous and completely fictitious, because the "decline" and
"recovery" were an artefact of how the prices were stored, not a real market event.
This is the scenario the raw/adjusted separation test is specifically designed to
prevent from propagating into Stage 2.

The second dangerous failure is a gap in the date coverage — a date range missing
from the cache, so the backtester works from incomplete data without realising it.
The incremental-start caching logic (tested in `test_caching`) and the per-ticker
isolation (tested in `test_partial_run_failure`) together ensure that the coverage
is consistent: either a full ticker's batch commits or none of it does, and the
start-date logic correctly extends the window on the next run. Stage 2 can safely
assume that if a date range is in the database, it is complete.

Stage 2 will introduce Alembic at this boundary. The data in the cache is now worth
protecting, and schema changes should migrate rather than rebuild.
