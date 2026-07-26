# Step 3 — Sacred Gate 1 (Lookahead and Cost Tests) (Stage 2)

## 1. What this does

`tests/backtester/test_sacred_gate.py` is the load-bearing test file for Stage 2.
Three test functions prove two properties of the backtesting engine:

**Gate 1a — no lookahead bias**: If future-knowing data reaches the backtester, the
Sharpe ratio is detectably inflated (> 3.0). If the same data is clean (no future
information), the Sharpe is in a realistic range, and is measurably lower than the
lookahead result.

**Gate 1b — transaction costs change outcomes**: Running the same strategy with
commission = 0.2% produces a strictly lower total return than running it with
commission = 0.0%.

Neither gate tests that the engine "prevents" every possible form of lookahead. What
they prove is more precise: (a) if lookahead data enters the backtester, the results
are detectably wrong, and (b) the cost parameter is actually applied. These are the
two empirical claims the project makes about the backtesting layer.

These tests do not use the database. All data is generated inline.

---

## 2. Every meaningful line explained

### Lookahead strategy

```python
class LookaheadStrategy(Strategy):
    def init(self):
        self.signal = self.I(lambda x: x, self.data.Signal, name="lookahead_signal")

    def next(self):
        if self.signal[-1] > 0.5 and not self.position:
            self.buy()
        elif self.signal[-1] <= 0.5 and self.position:
            self.position.close()
```

`LookaheadStrategy` lives in the test file, not in `src/`. It is intentionally wrong
by design and has no place in production code.

`self.I(lambda x: x, self.data.Signal, ...)` wraps the Signal column as a tracked
indicator. The lambda is the identity function — it passes the values through
unchanged. This is required because backtesting.py's bar-by-bar slicing only applies
to indicators registered through `self.I()`. Without wrapping, `self.data.Signal` in
`next()` would still work (backtesting.py slices all data columns), but `self.I()`
registers it for plots and stats, which is useful for debugging and makes the intent
clear: Signal is being used as an indicator, not raw price data.

`self.signal[-1] > 0.5` — the Signal column contains 0.0 or 1.0. The threshold of
0.5 is a clean way to read "Signal is 1" while being robust to any floating-point
values close to 0 or 1.

`and not self.position` — prevents a second buy call while already in a position.
Combined with `exclusive_orders=True` in the engine, this is belt-and-suspenders.

---

### Data helpers

```python
def _make_bt_data(seed: int = 42, n_bars: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    daily_returns = rng.normal(0.0005, 0.012, n_bars)
    close = 100.0 * np.exp(np.cumsum(daily_returns))
    dates = pd.bdate_range("2020-01-01", periods=n_bars)
    return pd.DataFrame(
        {
            "Open":   close * rng.uniform(0.997, 1.000, n_bars),
            "High":   close * rng.uniform(1.000, 1.010, n_bars),
            "Low":    close * rng.uniform(0.990, 1.000, n_bars),
            "Close":  close,
            "Volume": rng.integers(500_000, 2_000_000, n_bars).astype(float),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )
```

`np.random.default_rng(seed)` — the new NumPy random generator API (introduced in
NumPy 1.17). Unlike `np.random.seed()`, the `default_rng` generator is local to the
variable `rng` and does not affect the global random state. Two tests using
`default_rng(42)` are completely independent; neither's randomness affects the other.
The old `np.random.seed()` would have set global state, making test order matter.

`daily_returns = rng.normal(0.0005, 0.012, n_bars)` — 500 daily returns drawn from a
normal distribution with mean 0.05% (slight upward drift) and standard deviation
1.2% (daily vol). These parameters loosely mimic equity-like returns. The specific
values were chosen to produce a dataset where an SMA crossover fires enough times to
generate > 0 trades and where the returns are neither too large nor too small to
produce a degenerate backtest.

`close = 100.0 * np.exp(np.cumsum(daily_returns))` — constructs a price series from
log-returns. Cumulative sum of log-returns then exponentiated gives a geometric
random walk. Starting from 100.0 makes the initial price easy to reason about.
The alternative — `close = 100 + np.cumsum(returns * 100)` (arithmetic random walk)
— can produce negative prices if the cumulative return drops below -1.0, which would
crash backtesting.py. The geometric form cannot produce negative prices.

`pd.bdate_range` — business-day range. This produces dates that skip weekends, which
is correct for daily equity data. Using `pd.date_range` (calendar days) would include
Saturdays and Sundays, and backtesting.py would produce incorrect date calculations.

`Open/High/Low` are generated relative to `Close` with small random offsets
(`rng.uniform(...)`) to make the OHLC data internally consistent (High ≥ Close,
Low ≤ Close). Without this structure, backtesting.py's validation would reject the
data as malformed OHLC bars.

`Volume` is cast to `float`. Some versions of backtesting.py require Volume to be
float, not int.

---

```python
def _add_lookahead_signal(df: pd.DataFrame) -> pd.DataFrame:
    poisoned = df.copy()
    poisoned["Signal"] = (df["Close"].shift(-1) > df["Close"]).astype(float).fillna(0.0)
    return poisoned
```

`df.copy()` — creates an independent copy of the DataFrame. Without `.copy()`,
modifications to `poisoned` would silently modify `df` through pandas' view mechanism
(a common source of bugs). The clean data and the poisoned data must be independent
for the gate comparison to work.

`df["Close"].shift(-1)` — shifts the Close series one position backward (toward the
past). The result is a series where `shifted[i] = close[i+1]`. Comparing
`shifted[i] > close[i]` is equivalent to asking "will tomorrow's close be higher than
today's?" This is future-knowing information — exactly the lookahead attack being
simulated.

`.fillna(0.0)` — the last row of the shifted series is NaN (there is no
`close[n+1]` for the last bar). Filling with 0.0 means the strategy holds flat at
the last bar, which is neutral.

Why simulate the lookahead this way — at the data-preparation layer — rather than
inside the strategy? Because **this is the realistic failure mode**. Nobody writes a
strategy that directly calls `np.array(self.data.Close)[current_bar + 1]`. The
realistic mistake is an analyst computing an indicator using the full DataFrame before
passing it to the backtester, accidentally using a shifted or forward-looking window.
The gate simulates the actual attack vector.

---

### Gate 1a tests

```python
def test_lookahead_sharpe_is_implausibly_high():
    data = _add_lookahead_signal(_make_bt_data())
    result = run_backtest(data, LookaheadStrategy, ticker="LOOKAHEAD", commission=0.0, trade_on_close=True)
    assert result.sharpe_ratio > 3.0, ...
    assert result.num_trades > 50, ...
```

`commission=0.0` removes costs so they do not dilute the lookahead effect. The goal
is to show the maximum distortion that future-knowing data creates.

`trade_on_close=True` is required for the lookahead to register. At bar `i`,
`Signal[i] = 1` means `close[i+1] > close[i]`. For this signal to produce a winning
trade, we must buy at `close[i]` and the exit will be at `close[i+1]` (or later,
on the close signal). If instead we fill at the next bar's open (`trade_on_close=False`,
the default), we are betting that open[i+1] to close[i+1] is positive — which is a
different and weaker signal. With `trade_on_close=False`, the lookahead Sharpe was
only ≈ 1.45 during development, not distinguishably higher than a good legitimate
strategy. Only with `trade_on_close=True` does the Sharpe rise above 3.0.

`assert result.sharpe_ratio > 3.0` — real strategies on daily equity data rarely
sustain Sharpe > 2.0. A Sharpe > 3.0 on a 500-bar synthetic dataset without costs
is not plausible for a strategy with no future knowledge. It is the detection
threshold for "something is wrong with this data."

`assert result.num_trades > 50` — a belt check. If the strategy barely traded (say,
5 trades), the high Sharpe could be a statistical fluke from a tiny sample, not a
signal of lookahead. More than 50 trades on 500 bars means the strategy was active
and the Sharpe is not a small-sample artifact.

---

```python
def test_clean_strategy_sharpe_is_lower_than_lookahead():
    clean_data = _make_bt_data()
    poisoned_data = _add_lookahead_signal(clean_data)

    clean_result = run_backtest(clean_data, SMACrossover, ticker="CLEAN", commission=0.0)
    lookahead_result = run_backtest(poisoned_data, LookaheadStrategy, ticker="LOOKAHEAD",
                                   commission=0.0, trade_on_close=True)

    assert clean_result.sharpe_ratio < lookahead_result.sharpe_ratio, ...
```

Both datasets are generated from the same seed (42, the default) so the price data
is identical. The only difference is the lookahead signal column. The comparison is
clean and controlled.

`SMACrossover` without costs on a 500-bar upward-drifting random walk tends to
produce Sharpe in the range 0.3–1.2. `LookaheadStrategy` produces Sharpe > 3.0.
The gap is large enough that this assertion should be robust across seeds and bar
counts.

---

### Gate 1b

```python
def test_costs_reduce_returns():
    data = _make_bt_data()
    result_no_cost = run_backtest(data, SMACrossover, ticker="NOCOST", commission=0.0)
    result_with_cost = run_backtest(data, SMACrossover, ticker="WITHCOST", commission=0.002)

    assert result_with_cost.total_return_pct < result_no_cost.total_return_pct, ...
    assert result_with_cost.commission_pct == 0.002
    assert result_no_cost.commission_pct == 0.0
```

`commission=0.002` is 0.2% — double the default. The higher rate ensures the cost
effect is large enough to be unambiguous on 500 bars with moderate trade frequency.
Using 0.001 (the default) still reduces returns, but the margin is smaller. 0.002
makes the test robust to minor floating-point differences.

The assertion `result_with_cost.total_return_pct < result_no_cost.total_return_pct`
is the simplest possible cost test. It proves the commission argument is not silently
ignored by backtesting.py.

`assert result_with_cost.commission_pct == 0.002` proves the audit field in
`BacktestResult` is correctly populated. This matters because Stage 5 will use this
field when writing verdicts. If the field were always 0.0 regardless of what was
passed in, verdicts would incorrectly report the cost assumption.

---

## 3. Design decisions and rejected alternatives

### Using trade_on_close=True in the gate test

Using `trade_on_close=False` produces a lookahead Sharpe of ≈ 1.45 — within the
range of a legitimate high-performance strategy. The test would pass (1.45 > ???)
only if the threshold were lowered to something not meaningfully distinguishable from
a real result. The gate would cease to be a gate.

`trade_on_close=True` was necessary to make the signal's future-knowing property
actually manifest as profit. This required understanding backtesting.py's execution
timing model in detail — a timing bug in the test setup was discovered (and fixed)
during development, which itself validated that the gate is probing something real.

### Inline data generation rather than database-backed synthetic data

The test conftest (`tests/backtester/conftest.py`) provides a `seeded_db` fixture
that seeds the test database with synthetic price data for `test_data_loader.py`.
The gate tests deliberately do not use this fixture. They construct DataFrames
inline, bypassing the database entirely.

The reason: the gate tests are proving a property of the backtesting engine's
interaction with data. The database pipeline is irrelevant to that proof. Using
`seeded_db` would add a dependency on the test database being running and reachable —
a failure point that has nothing to do with what is being tested. If the database is
down, the gate should still pass. The gate proves something about the backtester, not
about the data pipeline.

### Threshold of 3.0 for lookahead Sharpe

3.0 was chosen because it is comfortably above the range of legitimate daily equity
strategies (empirically, most real strategies run in the 0.3–2.0 range) and
comfortably below the actual lookahead result (which runs in the 8–15 range on this
dataset with `trade_on_close=True` and `commission=0.0`). A threshold of 3.0 is not
aggressive — the actual results are far above it.

A lower threshold (e.g., 2.0) would increase the risk of a false positive if a very
good legitimate strategy were substituted for the clean comparison. A higher threshold
(e.g., 10.0) would make the test pass only for extreme lookahead and miss partial or
subtle future-data contamination.

---

## 4. Concepts introduced

### Lookahead bias

Lookahead bias means a backtest uses information that was not available at the time a
trading decision was made. The decision to buy on 2015-06-01 was made — in the
backtest — using data that existed after 2015-06-01. The backtest records a
profitable trade; in reality, the trade could never have been made because the
information did not exist yet.

Lookahead bias inflates every performance metric: Sharpe, win rate, total return.
The inflated numbers look like a real edge. There is no internal signal in the
backtest output that indicates lookahead has occurred — the numbers are internally
consistent, just wrong.

The most common form: computing an indicator over the full historical DataFrame
before passing it to the backtester. A rolling mean over the next 5 bars, a future
min/max, or even a pandas `shift(-1)` can all introduce lookahead silently.

The detection mechanism here: because a truly perfect predictor (knowing tomorrow's
direction with certainty) produces implausibly high performance, lookahead-corrupted
results stand out by their Sharpe ratios. The gate formalizes this intuition as an
assertion.

### Transaction costs as a correctness check

It is easy to assume that "setting commission=0.001" in the Backtest constructor is
just passing a number around and the costs are applied correctly. The Gate 1b test
proves this is actually true, not just assumed. If backtesting.py had a bug where
commission was applied to only one leg of a trade (entry but not exit), total return
would still be reduced with costs, just not by as much. The test checks the direction
of the effect, not the exact magnitude — which is correct because the magnitude
depends on the number of trades, which is not hardcoded.

---

## 5. How the verification gate was satisfied

The gate consists of three assertions, each testing one property:

| Assertion | What it proves | What it does NOT prove |
|---|---|---|
| Lookahead Sharpe > 3.0 | Future-knowing data produces detectably wrong results | That ALL forms of lookahead produce Sharpe > 3.0 |
| Clean Sharpe < Lookahead Sharpe | The discriminator actually discriminates | That the clean strategy has no lookahead |
| Cost reduces total return | Commission argument is applied | The exact cost per trade is correct |

**Residual risks stated explicitly:**

1. The gate proves lookahead is detectable via Sharpe when using `shift(-1)` as the
   attack vector. A different lookahead mechanism (e.g., computing a 5-bar forward
   rolling maximum) might produce a Sharpe of 2.5 — above a legitimate strategy but
   below the 3.0 threshold. The gate would not catch it.

2. The gate tests do not use real price data. The synthetic random walk has different
   statistical properties than real equity markets (no autocorrelation, no
   volatility clustering, no fat tails). Results on real data could differ.

3. The gate tests the engine's interaction with data it receives. It does not test
   the data pipeline itself. A bug in `load_price_data` that applied a shift to the
   Close column would not be caught by the gate — it would be caught by the data
   loader tests.

These residual risks are documented, not concealed. For the purpose of this project,
the gate provides adequate evidence that the engine behaves correctly under the most
common failure modes.

---

## 6. Interview defense

**Q: Why didn't you just use a known strategy like buy-and-hold as the control
instead of building an elaborate lookahead strategy?**

A: Buy-and-hold would test "does the engine run without crashing," not "does
lookahead inflate results." The gate needs to demonstrate that future-knowing
information produces a detectably different outcome. Only a strategy that actually
uses future information can demonstrate that future information changes the result.
A strategy that ignores the Signal column would show the same result regardless of
whether Signal is clean or poisoned.

**Q: You said trade_on_close=True was needed to make the gate work. Does that mean
you discovered a failure mode during development?**

A: Yes, exactly. When the gate was first implemented with `trade_on_close=False`, the
lookahead Sharpe was 1.45 — not implausibly high. This revealed something important:
the execution timing model of backtesting.py was not what I had assumed. A signal
computed from today's close must be filled at today's close to capture the
close-to-close movement that the signal predicts. Filling at tomorrow's open captures
a different movement (open-to-close the next day), which has no relation to the
prediction. Discovering this through a failing test is the correct outcome — the gate
was designed to be hard to pass, and it caught a real gap in understanding.

**Q (hard): You are claiming the backtesting engine has no lookahead bias. But your
proof uses a synthetic random walk with no autocorrelation. What if the engine has
a subtle lookahead bug that only manifests on real data with autocorrelation — for
example, a rolling indicator that subtly peeks at future data because of how pandas
handles window edges?**

A: That risk is real and the gate does not fully address it. The gate proves that a
gross lookahead (a pre-shifted column) produces detectably wrong results. A subtle
lookahead — for example, a rolling window that includes the current bar's future data
because of how an indicator is indexed — might produce a smaller but still real
distortion that the 3.0 threshold would miss. The honest mitigation is: (a) use
`self.I()` for all indicators inside strategies, which enforces bar-by-bar slicing;
(b) never compute indicators by directly indexing arrays in `next()`; (c) for any
strategy that produces an implausibly high Sharpe on real data, treat it as a red
flag and audit the indicator code before trusting the result. The gate is a
lower-bound proof, not a guarantee of zero lookahead in all possible strategies.

---

## 7. What comes next and why

Stage 3 introduces 2–3 documented strategies (from the literature) and a strategy
schema — a typed Pydantic model that represents a strategy rule. The gate's
significance for Stage 3 is that it establishes a reference point: any strategy that
produces Sharpe > 3.0 on a clean real-world dataset should be viewed with extreme
suspicion and audited for lookahead before any research conclusion is drawn.

If Gate 1 were not closed, the entire research program built in Stages 4 and 5 would
be suspect. The agent generates hypotheses and commissions backtests. If those
backtests had silent lookahead bias, every result would be inflated, every hypothesis
would be "confirmed," and the agent would produce confidently wrong verdicts with no
way to detect the error. The gate is the foundation that makes the honesty claim in
Stage 5 possible.
