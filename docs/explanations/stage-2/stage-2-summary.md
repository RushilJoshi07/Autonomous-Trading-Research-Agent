# Stage 2 Summary — Backtesting Engine

## 1. What Stage 2 is

Stage 2 delivers a complete, deterministic backtesting layer that sits between the
price data cache (Stage 1) and the strategy research program (Stages 3–5).

Before Stage 2, the project had raw and adjusted price history stored in Postgres
with a reliable ingestion pipeline, but no way to run a strategy over that history
and get a structured, auditable result. After Stage 2:

- `load_price_data` reads the price cache and produces a DataFrame in exactly the
  shape backtesting.py requires.
- `run_backtest` accepts that DataFrame and any `Strategy` subclass, runs a
  simulation, and returns a typed `BacktestResult` with all performance metrics and
  the cost assumption recorded.
- `SMACrossover` demonstrates the `Strategy` subclass pattern that Stage 3 strategies
  will follow.
- **Sacred Gate 1 is closed**: it has been empirically proved that (a) lookahead data
  produces detectably inflated Sharpe ratios, and (b) transaction costs are actually
  applied and change outcomes.

Stage 2 involves no LLM at all. It is a deterministic computation layer.

**What it is NOT:** Stage 2 does not write results to the database, does not define
the strategy schema (that is Stage 3), and does not call any external APIs or make
research claims. It is a pure function: same inputs → same outputs, always.

---

## 3. Cross-component design decisions

(Code-level decisions for each component are in the step explainers. This section
covers choices that span components.)

### The choice of backtesting.py over vectorbt

Covered in [step-02-engine-and-strategy.md](step-02-engine-and-strategy.md). The
core tradeoff: backtesting.py's bar-by-bar sequential model makes the data boundary
per bar explicit and makes accidental lookahead unnatural to introduce through the
API. vectorbt's vectorized model is faster but makes the same mistake natural and
invisible. For daily bars over a few years, performance is not the constraint.

This decision is load-bearing: it determines the implementation pattern for all
strategies in Stage 3 and fixes the `next()` + `self.I()` constraint that every
strategy must follow.

### The separation between data loading and backtesting

`load_price_data` is in `data_loader.py`; the backtester is in `engine.py`. A caller
could have written:

```python
def run_backtest(ticker, session, ...):
    df = load_price_data(ticker, session)
    bt = Backtest(df, ...)
```

This was rejected because the backtesting layer and the database layer are tested
independently. The gate tests construct DataFrames inline and pass them directly to
`run_backtest`, without any database involvement. If `run_backtest` internally called
`load_price_data`, the gate tests would either require a live database or would have
to patch out the loader — adding a dependency that is irrelevant to what the gate
is testing.

The separation also makes the engine reusable for any DataFrame in the correct shape,
not just ones from the database. A caller that computes synthetic data (for
hypothesis generation in Stage 5) can pass it directly to the engine.

### BacktestResult as the boundary type

`BacktestResult` is the Pydantic model that carries all backtest results. It will
become the return type of the backtester MCP tool in Stage 4, which means its schema
is effectively locked from this point. Any field added later is backward-compatible;
any field removed or renamed is a breaking change to the MCP tool and the verdict
validation layer in Stage 5.

The commission_pct audit field was included from the start specifically because the
agent verdict in Stage 5 must reference the cost assumption used. A number like
"Sharpe 1.4" is meaningless without knowing what costs were applied.

---

## 4. Concepts introduced (spanning components)

### Lookahead bias at the data-preparation layer

The most important concept from Stage 2. Covered fully in
[step-03-sacred-gate.md](step-03-sacred-gate.md). The key insight: lookahead
is not a bug in the backtesting library's `next()` loop — it is almost always
introduced at the layer above, where analysts compute indicators over the full
DataFrame before passing it to the backtester. A `shift(-1)`, a forward-looking
rolling window, or a label computed from future returns can all silently contaminate
every bar's signal with future information.

The gate formalizes the detection: if future-knowing data enters the backtester, the
Sharpe is implausibly high. Real strategies on daily equity data rarely exceed
Sharpe 2.0; a perfect predictor on 500 bars produces Sharpe > 3.0.

### Why "no lookahead" is a library property AND a data property

backtesting.py structurally prevents accessing future data through `self.data` and
`self.I()` indicators within `next()`. But it cannot prevent lookahead at the data
level — if a pre-shifted column is added to the DataFrame before passing it to the
engine, the library has no way to know the column contains future information. The
two defenses are complementary: use `self.I()` (structural) and close the gate
(empirical). Neither alone is sufficient.

---

## 5. Verification — the sacred gate, exhaustively

Sacred Gate 1 is the non-negotiable claim of Stage 2. The exact assertions and their
reasoning are covered in [step-03-sacred-gate.md](step-03-sacred-gate.md). This
section provides the honest accounting of what was proved and what was not.

### What Gate 1a proved

A strategy given a `Signal` column encoding `close[i+1] > close[i]` (tomorrow's
direction), run with `trade_on_close=True`, produces `sharpe_ratio > 3.0`. The same
data without the Signal column, run through `SMACrossover` (a legitimate strategy),
produces a Sharpe measurably and substantially lower.

This proves: **lookahead of the shift(-1) variety, executed at the correct bar's
close, produces results that are detectably wrong by a large margin.** The detection
mechanism is the Sharpe threshold.

### What Gate 1a did NOT prove

- It does not prove that lookahead executed at the next bar's open is detectable.
  (It is not: the gate was initially implemented with `trade_on_close=False` and the
  Sharpe was only 1.45, not distinguishable from a good strategy.)
- It does not prove that partial lookahead (e.g., a signal using close[i+1] only
  some of the time) would produce Sharpe > 3.0.
- It does not prove that forward-looking indicators other than `shift(-1)` produce
  Sharpe > 3.0. A 5-bar forward maximum might produce a smaller inflation that falls
  below the threshold.
- It does not prove the data pipeline is free of lookahead. That is tested by the
  data loader tests in Stage 1.

### What Gate 1b proved

Running `SMACrossover` with `commission=0.002` produces a strictly lower
`total_return_pct` than running it with `commission=0.0`, and `commission_pct` in the
result matches the value passed in.

This proves: **the commission argument is applied and not silently ignored.** The
`BacktestResult` audit field is correctly populated.

### What Gate 1b did NOT prove

- The exact per-trade cost arithmetic. We proved direction, not magnitude.
- That slippage (fill price deviating from the requested price) is modeled. It is
  not: backtesting.py's commission parameter covers round-trip cost but does not
  model market impact.
- That commissions compound correctly for multi-trade sequences. We tested aggregate
  return, not per-trade accounting.

### The full test run

All 16 tests pass as of the Stage 2 close: 7 data pipeline tests (Stage 1, which
must not regress) and 9 backtester tests (Stage 2).

---

## 6. Interview defense

**Q: What is the backtesting engine and why does it need to be separate from the
agent?**

A: The engine is a deterministic Python function: same inputs always produce the same
outputs. This separation is what makes the agent's claims verifiable. In Stage 5, the
agent synthesizes a verdict where every quantitative claim references a specific tool
call and its output. If the engine were itself reasoning or making choices — if it were
in any way non-deterministic — the verdict's claims could not be traced to the tool
output that produced them. The engine runs the computation; the agent decides what
computation to run. Those two responsibilities cannot be in the same component.

**Q: You said Sacred Gate 1 is "closed." What exactly does that mean?**

A: It means the two empirical claims that Stage 2 requires have been tested and pass.
"Closed" does not mean "impossible to circumvent." A determined person could introduce
lookahead that the gate would not detect — a subtle indicator using a forward window
might not push Sharpe above 3.0. "Closed" means the realistic failure mode has been
simulated (accidental `shift(-1)` at the data-preparation layer), the results were
shown to be detectably wrong, and the cost parameter has been shown to actually apply.
The gate is a lower-bound proof plus an empirical detection mechanism, not a
mathematical guarantee.

**Q (hard): You built the gate on synthetic random-walk data. Real equity data has
autocorrelation, fat tails, and volatility clustering. Could a strategy that happens
to be correlated with market structure appear to have a Sharpe > 3.0 on real data
without any lookahead, causing a false positive in your gate?**

A: Yes, that is possible in principle, though rare on daily equity data over 500 bars.
A strategy with a Sharpe > 3.0 on real multi-year daily data would be extraordinary —
funds publishing papers about strategies with Sharpe 1.5 attract significant capital.
In practice, if `run_backtest` on clean real data returns Sharpe > 3.0, the correct
response is to audit the strategy for lookahead before trusting the result. The gate
number is a red-flag threshold for "investigate this," not a mathematical proof of
the absence of lookahead. The honest defense is: the threshold was chosen to be above
the empirical range of legitimate daily equity strategies; it is a heuristic, stated
plainly as such.

**Q: Why didn't you introduce Alembic at Stage 2 as the architecture document
originally indicated?**

A: The architecture said "Stage 1→2 boundary." Stage 2 makes no schema changes — no
new tables, no column alterations. Alembic's value is managing migrations on a schema
that holds data you cannot afford to lose. A baseline Alembic setup with no actual
delta provides no more protection than `create_all()`. Stage 3 introduces new tables
(strategy definitions, backtest results stored in the database), which is the first
real migration — the right moment to set up Alembic so the tooling earns its keep on
day one. This was a deliberate decision recorded in the Stage 2 plan, not an
oversight.

---

## 7. What comes next and why

Stage 3 introduces the strategy schema: a Pydantic model that describes a trading
rule in structured data form — indicator name, parameters, entry condition, exit
condition. Stage 3 also adds 2–3 documented strategies whose results should be
consistent with the published literature.

If Stage 2 were wrong in any of these ways, Stage 3 and beyond would break:

- **If costs were silently dropped**: Stage 3 strategies would report returns that
  look better than published results (since published results typically include costs).
  The literature comparison gate would fail or, worse, pass on inflated numbers.

- **If load_price_data read raw instead of adjusted prices**: strategies would
  generate phantom trades at split dates (the fake 75% crash on AAPL's 2020 split).
  Stage 3's literature comparison would show anomalous results around those dates that
  would be mistaken for strategy signal.

- **If the engine were not deterministic**: the same strategy run twice would produce
  different Sharpe ratios. Stage 5's verdicts would be unreproducible, and the claim
  that "every quantitative claim traces to a tool output" would be meaningless if the
  tool output itself varied.

Stage 2 is the floor. Everything built above it stands on the assumption that the
backtester returns honest numbers. That assumption has now been empirically tested.
