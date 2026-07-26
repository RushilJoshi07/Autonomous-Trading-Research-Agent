# Step 2 — Backtesting Engine and SMA Crossover Strategy (Stage 2)

## 1. What this does

After [step-01-data-loader-and-result-model.md](step-01-data-loader-and-result-model.md)
built the bridge from the database to backtesting.py, these two files complete the
execution layer:

- `engine.py` — the single function that runs a backtest. It takes a DataFrame and
  a strategy class, delegates to backtesting.py, and returns a typed `BacktestResult`.
  This is the function every strategy in the project goes through.

- `sma_crossover.py` — a working example strategy. A simple moving-average crossover
  that buys when a short-period average crosses above a long-period average and sells
  on the reverse crossing. Its purpose is to give the engine a concrete strategy to
  run during tests and the sacred gate proof. It is not the strategy schema — that
  comes in Stage 3.

What these are NOT: neither file stores results to the database, calls an LLM, or
makes research claims. They are a deterministic computation layer. Same inputs always
produce the same outputs.

---

## 2. Every meaningful line explained

### engine.py

```python
_DEFAULT_COMMISSION = 0.001  # 0.1% per trade
_DEFAULT_CASH = 10_000
```

Module-level constants rather than inline literals. This matters because the gate
test deliberately overrides commission to `0.0` and `0.002` — if the default were
buried in the function signature as `commission=0.001`, a reader comparing the
no-cost and with-cost runs would have to trace back to the default to understand
what is being compared. With named constants, the default is visible and
documented in one place.

---

```python
def run_backtest(
    data: pd.DataFrame,
    strategy_cls: type[Strategy],
    ticker: str = "",
    commission: float = _DEFAULT_COMMISSION,
    cash: float = _DEFAULT_CASH,
    trade_on_close: bool = False,
    **strategy_params,
) -> BacktestResult:
```

`strategy_cls: type[Strategy]` — the function accepts a class, not an instance.
backtesting.py's `Backtest` constructor takes the strategy class and instantiates it
internally, applying `strategy_params` through `.run()`. If the function accepted an
already-instantiated strategy object, there would be no clean way to pass parameter
overrides.

`ticker: str = ""` — not used by the backtesting.py machinery at all; it is passed
straight through to `BacktestResult` as a label. The default is an empty string
rather than `None` so `BacktestResult.ticker` is always a `str` and downstream code
never needs to guard against `None`.

`trade_on_close: bool = False` — backtesting.py's default is to fill orders at the
next bar's open. This reflects how real EOD strategies work: you compute a signal
after market close, and your broker fills you at the next day's open. The `True`
option is explicitly exposed because the sacred gate test requires it — the lookahead
strategy uses a signal computed from a bar's close and must be filled at that same
close to demonstrate that the lookahead data is actually being used. Hiding this
parameter would make the gate test impossible to implement honestly. See section 3
for the full discussion.

`**strategy_params` — keyword arguments forwarded to `bt.run()`. This is how
backtesting.py handles parameter overrides at run time. The alternative — accepting
a `dict` — would require callers to write `run_backtest(..., params={"fast_period": 5})`
instead of `run_backtest(..., fast_period=5)`. The `**kwargs` pattern is standard
Python idiom for pass-through and is more readable at the call site.

---

```python
bt = Backtest(
    data,
    strategy_cls,
    commission=commission,
    cash=cash,
    exclusive_orders=True,
    finalize_trades=True,
    trade_on_close=trade_on_close,
)
```

Four arguments beyond data and strategy class, each with a specific reason:

**`commission=commission`** — the fraction charged per trade (both directions). If
omitted, backtesting.py defaults to zero commission. That would make every strategy
look better than it would be in reality. Passing it explicitly — and storing it in
`BacktestResult.commission_pct` — ensures the cost assumption is recorded alongside
the result and can be audited.

**`exclusive_orders=True`** — prevents the backtester from opening a new position
while one is already open. Without this, a strategy that calls `self.buy()` while
already holding a long position creates a second position (called pyramiding). For
`SMACrossover`, this would mean the strategy accumulates multiple long positions
whenever the fast SMA stays above the slow SMA — which is not what a simple crossover
strategy intends. With `exclusive_orders=True`, the second `buy()` call is silently
ignored. This is a guard against a common mistake in strategy coding, not a feature
of the strategy itself.

**`finalize_trades=True`** — tells backtesting.py to close any open position at the
last bar and include it in the performance statistics. Without this, a position that
happens to still be open at the end of the data window is excluded from the
statistics, and backtesting.py emits `UserWarning: Some trades remain open at the
end of backtest`. This produces three problems: the trade count is understated, the
win-rate calculation is based on fewer trades than actually occurred, and the warning
clutters test output. Finalizing is the honest choice — the position would have been
closed eventually; treating the last close price as the exit price is a fair estimate.

**`trade_on_close=trade_on_close`** — passed through from the function parameter.
Default is `False`.

---

```python
stats = bt.run(**strategy_params)
return BacktestResult.from_stats(stats, ticker=ticker, commission=commission)
```

`bt.run(**strategy_params)` passes the strategy parameters to the strategy instance.
backtesting.py applies them before calling `strategy.init()`, so `fast_period` and
`slow_period` are set on the instance by the time the strategy starts computing.
`commission` is passed separately to `from_stats` because it is not in the stats
Series that backtesting.py returns — it is the parameter we passed in, and we need it
in the result for the audit trail. See section 3 for why commission is stored
explicitly.

---

### sma_crossover.py

```python
class SMACrossover(Strategy):
    fast_period: int = 10
    slow_period: int = 30
```

`Strategy` is the base class from backtesting.py. All strategies must subclass it.
Class-level attributes with type annotations (`fast_period: int = 10`) are how
backtesting.py exposes strategy parameters that can be overridden via `bt.run()`. If
`fast_period` were defined as a plain instance attribute in `__init__`, backtesting.py
would not know about it and the override mechanism would not work.

The specific defaults — 10 and 30 — are common starting points for SMA crossovers
in the literature. They are not tuned values and make no claim about what is optimal.

---

```python
def init(self):
    close = self.data.Close
    self.fast = self.I(lambda s: pd.Series(s).rolling(self.fast_period).mean().values, close)
    self.slow = self.I(lambda s: pd.Series(s).rolling(self.slow_period).mean().values, close)
```

`self.data.Close` is a backtesting.py `_Array` object — a wrapper around the price
data that exposes it up to the current bar in `next()`. In `init()`, it contains the
full series (backtesting.py uses the whole series to precompute indicators in `init()`
and then slices them per bar in `next()`).

`self.I(func, *args)` is the indicator wrapper that backtesting.py requires for all
indicators computed inside a strategy. It does three things: it calls `func(*args)`
once during `init()` to compute the full indicator series; it wraps the result in a
`_Array` so `next()` can access it with correct bar-by-bar slicing; and it registers
the indicator for plotting and stats output.

The lambda `lambda s: pd.Series(s).rolling(self.fast_period).mean().values` takes
the raw array `s`, wraps it as a pandas `Series` (so `.rolling().mean()` is
available), computes the rolling mean, and returns `.values` (a numpy array). This
conversion to `.values` is required because `self.I` expects a numpy array back, not
a pandas Series.

Why `self.I()` instead of computing the SMA directly? This is explained in section 3,
but the short answer is: without `self.I()`, the indicator is computed as a full-history
numpy array that `next()` accesses by raw index — there is no bar-by-bar slicing
enforcement, and it is easy to accidentally access the wrong bar. `self.I()` provides
that enforcement automatically.

---

```python
def next(self):
    if crossover(self.fast, self.slow):
        self.buy()
    elif crossover(self.slow, self.fast):
        self.position.close()
```

`next()` is called once per bar after the warm-up period (i.e., after enough bars
have elapsed to compute both SMAs — here, after `slow_period` bars). On each call,
`self.fast` and `self.slow` are `_Array` objects sliced to the current bar.

`crossover(a, b)` from `backtesting.lib` returns `True` if and only if the previous
bar had `a < b` AND the current bar has `a > b`. This is a two-bar comparison. It is
NOT true for every bar where `a > b` — only on the specific bar where the crossing
happened. This is the critical detail: see section 3.

`self.buy()` opens a long position using all available cash at the current bar's fill
price (next open, or current close if `trade_on_close=True`). There is no size
argument — backtesting.py allocates all available cash by default, which is
appropriate for a simple single-position strategy.

`self.position.close()` closes the currently open position, if any. The `elif`
prevents a close attempt when there is no open position. `position.close()` on an
empty position is a no-op in backtesting.py, but the `elif` makes the intent explicit.

---

## 3. Design decisions and rejected alternatives

### exclusive_orders=True

Without `exclusive_orders=True`, a strategy that calls `self.buy()` while already
holding a long position creates an additional position — the backtester interprets
it as an intent to pyramid (add to a winning position). For `SMACrossover`, the fast
SMA remains above the slow SMA for many consecutive bars once a crossover occurs. If
`next()` re-evaluates `crossover(self.fast, self.slow)` on bar 50, it returns `False`
(no crossing happened at that bar). But a naive implementation might check
`self.fast[-1] > self.slow[-1]` instead of `crossover(...)`, and that would be True
on every bar in an uptrend, causing repeated buys.

The correct fix is `crossover()` in the strategy (explained below). `exclusive_orders`
is the belt-and-suspenders defense: even if a strategy has a bug that issues a
redundant buy, it is silently ignored rather than creating an unintended position.
The cost of using `exclusive_orders=True` is that pyramid strategies — which
intentionally add to positions — cannot use `run_backtest()` as written. Those
strategies are out of scope for Stage 3.

### finalize_trades=True

The alternative is the backtesting.py default: omit `finalize_trades`, in which case
open trades at the end of the data window are excluded from all statistics. The
consequence is subtle: if the last trade opened on bar 490 of 500 and is still open
at bar 500, the win-rate, average return, and Sharpe calculations are based on the
other trades only. If the last trade happens to be a winner, the statistics are
pessimistically biased. If it is a loser, they are optimistically biased. The bias
is always present when `finalize_trades=False`; its direction depends on luck.

Using the last bar's close as the exit price for an open trade is an approximation —
in reality, the trader would exit at some future price. But for a backtesting tool
whose primary purpose is comparing strategies against each other and against a
control, the approximation is neutral (it applies equally to every strategy that
happens to be in a trade at the data window's end) and is far less harmful than
systematically excluding trades from statistics.

### trade_on_close as an explicit parameter

The default `False` reflects real-world trading: you observe today's close price,
compute your signal, and your broker fills you at tomorrow's open. Most legitimate
strategies should use `trade_on_close=False`.

The `True` option exists because the sacred gate test requires it. The
`LookaheadStrategy` uses a `Signal` column pre-computed with `shift(-1)`: at bar `i`,
`Signal[i] = 1` means "tomorrow's close will be higher than today's close."
This signal has genuine predictive edge only when executed at today's close — if the
fill happens at tomorrow's open, the strategy is betting that close-to-open movement
will also be positive, which is a different and weaker signal. With
`trade_on_close=False`, the gate strategy would produce Sharpe ≈ 1.5 instead of
> 3.0, and the test would not distinguish lookahead from a lucky random strategy.

Hiding `trade_on_close` by always passing `False` to the Backtest constructor would
make the gate test impossible to implement honestly. Exposing it as an explicit
parameter makes the gate test's design transparent: "this strategy executes at the
bar's close because that is the only way to exploit a signal about that bar's close."

### self.I() for indicator computation

The alternative — computing the SMA directly as a numpy array in `init()` and
storing it as an instance attribute — looks like this:

```python
def init(self):
    closes = np.array(self.data.Close)
    self.fast = pd.Series(closes).rolling(self.fast_period).mean().values
    # fast is now a plain numpy array of length N

def next(self):
    current_bar = len(self.data) - 1
    if self.fast[current_bar] > self.slow[current_bar]:
        ...
```

This requires manually tracking which bar is current, and any off-by-one error in
the index silently accesses the wrong bar. More importantly, the raw array is
accessible at all indices from `next()` — there is nothing preventing the strategy
from accidentally reading `self.fast[current_bar + 5]` (future data). The data is
physically present in the array; the strategy author has to choose not to read it.

`self.I()` wraps the result in a `_Array` that slices to the current bar just like
`self.data.Close` does. In `next()`, `self.fast[-1]` is the current bar's SMA;
`self.fast[-2]` is the previous bar's SMA. There is no index beyond the current bar.
The containment is enforced by the data structure.

An important subtlety: `self.I()` does NOT prevent the lambda passed to it from
seeing future data at computation time (during `init()`). The lambda receives the
full close array and can compute a rolling mean over all of it. What `self.I()`
prevents is future-data access *within `next()`* — the bar-by-bar slicing.
This distinction is important for understanding what the gate test proves (see
section 5).

### crossover() instead of a direct comparison

The naive implementation of the buy condition is:

```python
if self.fast[-1] > self.slow[-1] and not self.position:
    self.buy()
```

This works correctly with `not self.position` as a guard, because it only buys when
not already in a position. But it has a different subtle problem: it does not
distinguish "fast just crossed above slow this bar" from "fast has been above slow
for 30 bars." Both conditions trigger the buy. Combined with `exclusive_orders=True`,
this is behaviorally equivalent to `crossover()` for a single-position strategy. But
the intent is wrong: the strategy is supposed to react to the crossing event, not to
the state of being crossed.

`crossover(a, b)` precisely encodes the event: it reads `a[-2] < b[-2]` (last bar:
a was below b) AND `a[-1] > b[-1]` (this bar: a is above b). It returns `True` only
once — on the specific bar the crossing occurs. This is the correct intent, and it
is the version a hiring manager reviewing the code would expect to see.

---

## 4. Concepts introduced

### Bar-by-bar simulation vs. vectorized computation

backtesting.py simulates the strategy sequentially: it calls `next()` once for each
bar in the data, exposing only that bar and all prior bars, and asks "what would you
do right now?" This matches how real trading actually works — the strategy sees
today's price and decides to buy or sell, with no knowledge of tomorrow.

Vectorized computation — the approach used by vectorbt and common in pandas code —
computes over the entire array at once. For example, `signal = (close.shift(-1) >
close)` computes the signal for every bar simultaneously, including tomorrow's close
in today's computation. This is efficient but makes lookahead easy to introduce
accidentally.

The bar-by-bar model makes it structurally harder (though not impossible, as the gate
test demonstrates) to introduce lookahead. The risk shifts from "did the library
prevent this" to "did the person preparing the data columns introduce a shift."

### Pyramiding

Pyramiding means adding to an open position — buying more of an asset you already
hold long, or selling more of an asset you are already short. It is a legitimate
strategy (adding to winners) but must be explicitly intended. An SMA crossover
strategy that pyramids by accident — because the strategy code calls `buy()` every
bar the fast SMA is above the slow — will produce results that overstate capital
usage and distort the performance statistics, since equity is compounding differently
than the strategy designer assumed.

### The warm-up period

`next()` is not called for the first `slow_period - 1` bars. This is because there
are not enough bars to compute the slow SMA — a 30-period rolling mean requires at
least 30 bars. backtesting.py handles this automatically: it calls `next()` starting
from the bar where all indicators have a valid (non-NaN) value. The strategy author
does not need to check `if len(self.data) >= self.slow_period` — but they should know
this is happening, because it means the strategy never trades in the first 29 bars
of any dataset.

---

## 5. How the verification gate was satisfied

These two files are not themselves the gate — the gate lives in `test_sacred_gate.py`
(covered in [step-03-sacred-gate.md](step-03-sacred-gate.md)). But the gate depends
entirely on these two files working correctly.

The engine tests in `tests/backtester/test_engine.py` verify:
- `run_backtest` returns a `BacktestResult` (not a raw stats Series, not None)
- All required fields are populated
- `num_trades > 0` — the strategy actually traded on the synthetic dataset
- `sharpe_ratio` is a finite float (not NaN, not inf)
- `commission_pct` matches what was passed in

The strategy tests verify:
- `SMACrossover` runs without error on synthetic OHLCV data
- The trade count is positive (crossovers actually occurred in 500 bars with
  `fast_period=10, slow_period=30`)

**What these tests do NOT prove:** that the strategy produces the "correct" Sharpe
or return for a given dataset. Those values depend on the random seed, the parameter
values, and the data. Asserting a specific Sharpe value would make the test brittle
and would not catch real bugs — only changes to the random seed. The test asserts
structural correctness (right type, right shape, plausible values), not specific
numbers.

---

## 6. Interview defense

**Q: Why did you choose backtesting.py over vectorbt for the engine?**

A: The bar-by-bar sequential model in backtesting.py makes the execution timing
explicit and auditable. Each bar's `next()` call sees only that bar and its history.
vectorbt computes over arrays of the entire dataset simultaneously, which is faster
but makes accidental lookahead easier to introduce — you can slice an indicator array
with `[:-1]` and nothing warns you that you're peeking at the bar after the current
one. The tradeoff is performance: backtesting.py runs one strategy at a time and is
not suitable for parameter sweeps over thousands of combinations. That constraint was
acceptable given that this project runs one strategy per study, not grids.

**Q: Why didn't you just use pandas_ta to compute the SMA inside the strategy?**

A: You can use pandas_ta, but you must wrap the result in `self.I()`. If you compute
the SMA using pandas_ta on `self.data.Close` and store the raw array, it is not
sliced per bar in `next()`. The strategy can accidentally access any index in that
array, including future bars. Using pandas_ta through `self.I()` is fine; using it
outside `self.I()` and expecting bar-by-bar safety is a mistake. The lambda approach
in `SMACrossover` makes the requirement explicit — `self.I()` is not optional.

**Q (hard): Your gate test proves that a strategy given a pre-shifted signal column
produces an implausibly high Sharpe. But that proves the signal is being used, not
that the backtesting engine prevents lookahead. What if I wrote a strategy that
calls `np.array(self.data.Close)[len(self.data)]` — accessing future data through
the numpy array directly? Would your engine catch that?**

A: No. backtesting.py's `_Array` slicing protects access through `self.data.Close`
and through `self.I()` indicators. If the strategy directly converts `self.data.Close`
to a numpy array and indexes it beyond the current bar, backtesting.py cannot stop
it — the data is physically there. The honest answer is: we cannot prevent a
determined strategy author from introducing lookahead through raw array access. What
the engine and gate provide is (a) a structural API that makes the right thing
natural and the wrong thing unnatural, and (b) a detection mechanism — if lookahead
data is present, the Sharpe is detectably inflated. Strategies in this project are
written by the project author; the risk of adversarial lookahead is low. The
realistic failure mode is accidental lookahead at the data-preparation layer (a
`shift(-1)` in indicator computation), and that is exactly what the gate simulates
and proves detectable.

**Honest weakness:** `exclusive_orders=True` hides pyramid-strategy bugs rather than
surfacing them. A strategy that mistakenly re-buys would silently have the extra buy
ignored rather than showing an unusual trade count. For multi-position or scale-in
strategies, the engine would need to be called with `exclusive_orders=False`, and
the caller would need to handle position sizing explicitly. This is a documented
limitation, not an oversight.

---

## 7. What comes next and why

Stage 3 introduces a **strategy schema** — a Pydantic model that describes a strategy
rule in a structured data format (indicator name, parameters, entry condition, exit
condition). The current engine accepts any `Strategy` subclass and any parameters.
In Stage 5, the agent will emit a structured rule (a Pydantic model); a code layer
will translate that model into a concrete `Strategy` subclass and call `run_backtest`.
That translation layer cannot exist until there is a schema to translate.

If the engine were wrong in a way that silently underreports costs (for example, if
`commission_pct` were stored as 0.0 regardless of what was passed in), the agent in
Stage 5 would produce verdicts claiming low-cost edges that are actually being
measured cost-free. The error would not show up until someone noticed that strategies
confirmed at 0.1% commission looked the same as those confirmed at 0.5% commission.
The `test_costs_reduce_returns` assertion in the gate test specifically prevents this.
