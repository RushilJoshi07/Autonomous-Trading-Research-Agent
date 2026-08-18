# Step 6 — Statistics Tool (Stage 4)

> **Addendum (Component 8, same stage):** the entry-probability calibration
> this document describes below (`entry_prob = n_trades / len(data)`) was
> found to be substantively broken while writing Component 8's formal test
> suite — not just imprecise, but capable of producing the *wrong
> statistical conclusion*. Measured directly: for `sma_10_30_crossover`
> against real AAPL data (this component's own verification case), the
> broken calibration's null distribution averaged a Sharpe of ~0.58,
> against the observed 0.678, giving p≈0.33 ("not significant"). The
> corrected calibration — described in
> `docs/explanations/stage-4/step-08-formal-test-suite.md`, which replaces
> the probability-based approach with one anchored to the real strategy's
> own historical exit bars for any rule with a data-dependent `rule.exit`
> — gives a null distribution averaging Sharpe ~0.27, and p≈0.0099
> ("significant at the 1% level"). Every claim below describing the
> probability-based approach as this tool's mechanism for `rule.exit is not
> None` rules is now historical, not current; `test_significance` still
> uses the mechanism described here for `exit_after_bars`-only rules, where
> it was confirmed to remain correct. Left unedited below rather than
> rewritten, so the record of what was actually built, tested, and
> approved at this point in the stage stays intact — the correction is
> documented in full, including the mechanism that caused it, in Component
> 8's own step file.

## 1. What this does

This is the largest component in Stage 4, and the first to genuinely
implement something `docs/architecture.md` calls a hard requirement rather
than a convenience: §5 Step 3's mandatory control — "the question is never
'did this make money' but 'did it beat randomized entries at the same trade
frequency.'" Three MCP tools land here. `test_significance` runs that exact
comparison as a real statistical test: it backtests a `StrategyRule` for
real, backtests hundreds of randomized-entry variants of the same rule, and
reports a p-value for whether the real strategy's Sharpe ratio genuinely
beats that random-control distribution. `confidence_interval` bootstraps a
confidence interval for a strategy's mean per-trade return, resampled at
the trade level rather than the daily-bar level. `correct_p_values` applies
the Benjamini-Hochberg multiple-comparisons correction to a list of
p-values — the deterministic half of the defense against an agent that
tests enough hypotheses to get a false positive by chance (§5 Step 5).

Getting here required touching more of the existing codebase than any prior
component this stage: `backtester/result.py` (Stage 2/3) gained a new
field, `backtester/strategies/rule_strategy.py` (Stage 3) was refactored to
share logic with a new sibling module, and an entirely new top-level
package, `research_stats/`, was created. None of that was optional scope
creep — each piece was a genuine prerequisite for making the significance
test and the confidence interval actually computable, not something added
for its own sake.

What this component is *not*: it does not decide what significance level
counts as "good enough," does not track how many hypotheses have been
tested under a research charter (that's explicitly Stage 5's job, since it
needs the agent loop's persistent state, which doesn't exist yet), and does
not choose which strategies to test. It answers the specific statistical
questions it's asked, using real backtests as evidence, and reports numbers
— nothing here decides what those numbers mean for a research verdict.

---

## 2. Every meaningful line explained

### `backtester/result.py` — `trade_returns`

```python
trade_returns: list[float] = Field(default_factory=list)
```

A new field on `BacktestResult`, defaulting to an empty list so any
existing code constructing a `BacktestResult` without it (tests, mainly)
keeps working unchanged.

```python
trades = stats.get("_trades")
trade_returns = trades["ReturnPct"].tolist() if trades is not None else []
```

`backtesting.py`'s own `stats` object — the thing `from_stats` has always
parsed for summary numbers — carries a `_trades` key holding a full
per-trade `DataFrame`, previously read from but never actually extracted
for its row-level detail. This was confirmed directly before writing this
line, not assumed from `backtesting.py`'s documentation: a real 44-trade
AAPL SMA-crossover run was inspected, and `stats['_trades']` came back as
an actual `pandas.DataFrame` with a `ReturnPct` column holding each
individual trade's return as a fraction. `.tolist()` converts that column
into the plain Python floats `trade_returns: list[float]` expects — nothing
from `backtesting.py`'s own DataFrame type needs to survive past this line.
The `is not None` guard exists because `stats.get("_trades")` — the same
lenient accessor `_f`'s helper functions elsewhere in this file already use
for missing keys — would otherwise let a `None` propagate into
`None["ReturnPct"]` and crash with a much less informative error than an
empty list would.

### `rule_strategy.py`'s four extracted helpers

```python
def unique_terms(*term_lists: list[IndicatorTerm]) -> dict[IndicatorKey, IndicatorTerm]:
    result: dict[IndicatorKey, IndicatorTerm] = {}
    for terms in term_lists:
        for term in terms:
            result.setdefault(indicator_key(term), term)
    return result
```

Takes any number of term lists (previously `make_rule_strategy` always
passed exactly `entry_terms + exit_terms`, concatenated) and dedups them
into one map, keyed by `indicator_key(term)` — the same key function
`evaluator.py` already uses to identify "the same indicator+params
combination," so an indicator referenced in both an entry and exit
condition (or, now, potentially not referenced in an entry at all) only
gets computed once. `*term_lists` (variadic, not a single concatenated
list) is what lets `make_random_entry_strategy` later pass just
`exit_terms` alone, with nothing to concatenate.

```python
def indicator_usage(terms: dict[IndicatorKey, IndicatorTerm]) -> tuple[list[str], list[str]]:
    used_names = {term.name for term in terms.values()}
    indicators_used = sorted(n for n in used_names if ALL_INDICATORS[n].tier == "core")
    extended_indicators_used = sorted(n for n in used_names if ALL_INDICATORS[n].tier == "extended")
    return indicators_used, extended_indicators_used
```

Unchanged logic, just lifted out of `make_rule_strategy`'s body so both
strategy-compiling functions can populate the same provenance fields
(`indicators_used`/`extended_indicators_used`, Stage 3 Component 6's own
addition to `BacktestResult`) from whatever `unique_terms` map they built —
a random-entry control's provenance should still be inspectable the same
way a real strategy's is, even though it's only ever showing what
`rule.exit` depends on.

```python
def wire_indicators(strategy: Strategy, terms: dict[IndicatorKey, IndicatorTerm]) -> dict[IndicatorKey, str]:
```

The single most subtle piece of logic in this file, moved verbatim — every
line inside is unchanged from `make_rule_strategy`'s old `init()` body,
including the closure-capture pattern (`_spec=spec, _name=term.name` as
default arguments, binding each loop iteration's values rather than letting
a shared closure variable drift) and the named-attribute storage
(`self._ind_0`, `self._ind_1`, ...) that `_RuleBarContext`'s own docstring
already explains is required — `backtesting.py`'s per-bar re-slicing only
discovers indicators stored as direct instance attributes, not ones living
inside a dict. Extracting this as a standalone function changes nothing
about *how* it works, only *how many places* need to know it works that
way: one, now, instead of needing a second, subtly-different copy inside a
new `RandomEntryStrategy.init()`.

```python
def apply_exit(strategy: Strategy, rule: StrategyRule, ctx: BarContext) -> None:
    if rule.exit is not None and evaluate_condition(rule.exit, ctx):
        strategy.position.close()
        return
    if rule.exit_after_bars is not None and strategy.trades:
        bars_held = (len(strategy.data) - 1) - strategy.trades[-1].entry_bar
        if bars_held >= rule.exit_after_bars:
            strategy.position.close()
```

Also unchanged logic, moved out of `next()`'s second half. This function is
the one piece of `make_rule_strategy` a random-entry control *must* call
identically — the entire statistical premise of the significance test is
that only entry timing differs between the real strategy and its control,
so the exit logic has to be the literal same code path, not a
re-implementation that could accidentally diverge.

### `make_rule_strategy`, after the refactor

```python
def make_rule_strategy(rule: StrategyRule) -> type[Strategy]:
    entry_terms = _collect_indicator_terms(rule.entry)
    exit_terms = _collect_indicator_terms(rule.exit) if rule.exit is not None else []
    terms = unique_terms(entry_terms, exit_terms)
    indicators_used, extended_indicators_used = indicator_usage(terms)

    class RuleStrategy(Strategy):
        def init(self) -> None:
            self._key_to_attr = wire_indicators(self, terms)
            self._ctx: BarContext = _RuleBarContext(self)

        def next(self) -> None:
            if not self.position:
                if evaluate_condition(rule.entry, self._ctx):
                    self.buy()
                return
            apply_exit(self, rule, self._ctx)
```

Four calls into the extracted helpers replace what used to be roughly 40
lines of inlined logic. Confirmed to be byte-identical in behavior, not
just "should be" — the full existing 170-test suite was run immediately
after this refactor alone, before `random_entry_strategy.py` or
`research_stats/` existed at all, specifically to isolate whether the
refactor itself changed anything before any new code built on top of it.

### `backtester/strategies/random_entry_strategy.py`

```python
def make_random_entry_strategy(rule: StrategyRule, n_trades: int, seed: int) -> type[Strategy]:
    exit_terms = _collect_indicator_terms(rule.exit) if rule.exit is not None else []
    terms = unique_terms(exit_terms)
    indicators_used, extended_indicators_used = indicator_usage(terms)
```

Deliberately omits `rule.entry`'s terms entirely — this strategy's entry
decision is a coin flip, not an evaluated condition, so any indicator
`rule.entry` references is simply irrelevant here and never gets computed,
saving real work as a side effect of correctness rather than as a
deliberate optimization.

```python
    class RandomEntryStrategy(Strategy):
        def init(self) -> None:
            self._key_to_attr = wire_indicators(self, terms)
            self._ctx: BarContext = _RuleBarContext(self)
            self._rng = np.random.default_rng(seed)
            self._entry_prob = n_trades / len(self.data)
```

The same `wire_indicators`/`_RuleBarContext` pairing `make_rule_strategy`
uses, applied to a different (smaller) `terms` map. `self._rng =
np.random.default_rng(seed)` creates one independent, deterministic random
stream per compiled strategy instance — `numpy`'s modern `Generator` API,
not the older, globally-shared `np.random.seed()` pattern, specifically so
multiple `RandomEntryStrategy` instances (as the significance test will
create many of) never share or interfere with each other's randomness.
`self._entry_prob = n_trades / len(self.data)` is the calibration this
component's design settled on — see section 3 for why this specific
formula and not an alternative.

```python
        def next(self) -> None:
            if not self.position:
                if self._rng.random() < self._entry_prob:
                    self.buy()
                return
            apply_exit(self, rule, self._ctx)
```

Compare directly against `RuleStrategy.next()` above — the two functions
are now identical except for one line: `evaluate_condition(rule.entry,
self._ctx)` becomes `self._rng.random() < self._entry_prob`. Everything
else, including the shared `apply_exit` call, is the same code path. That
structural parallel is not incidental — it's the entire point of the
refactor in section 3 below.

### `research_stats/significance.py`

```python
_DEFAULT_N_RESAMPLES = 300
_MAX_RETRIES_PER_DRAW = 20
```

Both are named constants rather than inline literals, for the same reason
`MAX_LOOKBACK` and `REGIME_LOOKBACK_BARS` are — a single, easily-revisited
place to change either number later, rather than a magic number buried in
a function body. Why `300` specifically, not the plan's original `999`
guess, is section 3's third decision. `_MAX_RETRIES_PER_DRAW = 20` is a
generous cap on the zero-trade retry logic discussed next — large enough
that a genuinely reachable trade frequency essentially never exhausts it,
small enough that a truly pathological case (an `n_trades` far too high for
the data's length) fails loudly instead of retrying indefinitely.

```python
    observed = run_backtest(price_data, make_rule_strategy(rule), ticker=ticker, **kwargs)
    if observed.num_trades == 0:
        raise ValueError(
            f"cannot test significance: {rule.name!r} produced 0 trades against this "
            "data — no basis for a randomized-entry comparison"
        )
```

The real strategy is backtested exactly once, using the exact same
`run_backtest`/`make_rule_strategy` pair Component 3's own `run_backtest`
tool already uses — no separate code path for "the strategy this test is
about" versus "the strategy Component 3's tool runs." Zero real trades is
rejected immediately, before any random control is even attempted — there
is no meaningful "did it beat random entries at the same frequency"
question to ask when "the same frequency" is zero.

```python
    def rvs(size: tuple[int, ...] | int) -> np.ndarray:
```

This function's signature and body are the direct result of a real bug
found by testing, not the first version written — the full story, including
why an earlier offline check didn't catch it, is section 3's second
decision and section 5's verification narrative.

```python
        n = size[0] if isinstance(size, tuple) else size
        sharpes = np.empty(n)
        next_seed = seed
        for i in range(n):
            for attempt in range(_MAX_RETRIES_PER_DRAW):
                control_cls = make_random_entry_strategy(rule, observed.num_trades, seed=next_seed)
                next_seed += 1
                control = run_backtest(price_data, control_cls, ticker=ticker, **kwargs)
                if control.num_trades > 0:
                    break
            else:
                raise ValueError(
                    f"random-entry control produced 0 trades in {_MAX_RETRIES_PER_DRAW} "
                    f"consecutive attempts (observed_num_trades={observed.num_trades} may be "
                    "too low relative to the data length)"
                )
            sharpes[i] = control.sharpe_ratio
        return sharpes.reshape(size)
```

`next_seed` is a single running counter, incremented on every attempt —
including retries — never reused. This guarantees every one of the
potentially `n * _MAX_RETRIES_PER_DRAW` random-entry backtests this
function could run gets its own unique, deterministic seed, so the whole
procedure is exactly reproducible given the same top-level `seed`, no
matter how many retries happened to fire along the way. The `for...else`
(Python's `else` clause on a `for` loop, which runs only if the loop
completed without `break`) is what makes "ran out of retries" and "found a
valid draw" mutually exclusive and exhaustive — there's no third,
accidentally-unhandled case. `sharpes.reshape(size)` returns the array
shaped exactly as `size` was originally given, not just as a flat 1-D
array — necessary because `size` can be a multi-element tuple like `(300,
1)`, and `scipy.stats.monte_carlo_test` expects `rvs`'s return to match
that shape precisely.

```python
    mc = monte_carlo_test(
        data=[observed.sharpe_ratio],
        rvs=rvs,
        statistic=lambda x: x,
        n_resamples=n_resamples,
        alternative="greater",
    )
```

`data=[observed.sharpe_ratio]` — a length-1 list wrapping the single
observed value, not a raw sample of many observations. `statistic=lambda
x: x` — the identity function, because there's nothing to compute *from*
`data`; the value being tested *is* the statistic. `alternative="greater"`
is a one-sided test, matching the actual question architecture.md poses —
"did it beat," a directional claim, not "is it different from," which
would call for `"two-sided"`.

```python
    null_dist = mc.null_distribution.reshape(-1)

    return SignificanceResult(
        observed_sharpe=observed.sharpe_ratio,
        observed_num_trades=observed.num_trades,
        p_value=mc.pvalue.item(),
        n_resamples=n_resamples,
        null_mean_sharpe=float(np.mean(null_dist)),
        null_std_sharpe=float(np.std(null_dist)),
    )
```

`mc.pvalue` and `mc.null_distribution` both come back from `scipy` shaped
as arrays (confirmed directly — a length-1 `.pvalue` array and a
`(n_resamples, 1)`-shaped `.null_distribution`, matching `data`'s own
length-1 shape), not plain Python scalars — `.item()` extracts the single
p-value as a real float, and `.reshape(-1)` flattens the null distribution
to a plain 1-D array before computing its mean and standard deviation.
Reporting `null_mean_sharpe`/`null_std_sharpe` alongside the bare p-value
is a deliberate transparency choice — it lets a caller (a human now, an
agent from Stage 5) see *what the null distribution actually looked like*,
not just the single number derived from comparing against it.

### `research_stats/confidence.py`

```python
def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[np.ndarray], float] = np.mean,
    confidence_level: float = 0.95,
    seed: int = 0,
) -> ConfidenceIntervalResult:
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        raise ValueError(f"bootstrap_ci needs at least 2 values, got {arr.size}")
```

The explicit size check exists because `scipy.stats.bootstrap` itself
requires at least two observations to have any resampling variance to
estimate — checking for it here, with a message naming the actual count
received, is strictly more useful to a caller than letting `scipy` raise
its own, more generic error two function calls deeper.

```python
    result = bootstrap(
        (arr,),
        statistic,
        confidence_level=confidence_level,
        method="BCa",
        rng=np.random.default_rng(seed),
    )
```

`(arr,)` — a one-element tuple wrapping the array, matching
`scipy.stats.bootstrap`'s expected "sequence of one or more samples" shape
even for the single-sample case this function always uses.
`method="BCa"` (bias-corrected and accelerated) is `scipy`'s own default
and the standard, more accurate choice over the simpler percentile method
for skewed distributions — trade returns are exactly the kind of
distribution (bounded below by -100%, unbounded above) BCa's bias
correction is meant for.

### `research_stats/multiple_comparisons.py`

```python
def correct_p_values(p_values: Sequence[float], method: str = "bh") -> MultipleComparisonsResult:
    adjusted = false_discovery_control(list(p_values), method=method)
    return MultipleComparisonsResult(
        p_values=list(p_values),
        adjusted_p_values=adjusted.tolist(),
        method=method,
    )
```

The thinnest function in this component — `scipy.stats.false_discovery_control`
already does everything needed. Echoing the original `p_values` back in the
result, alongside the adjusted ones, is a small but deliberate choice: a
caller reading only the response doesn't need to have separately tracked
which adjusted value corresponds to which original one.

### The three new MCP tools

```python
from research_stats.significance import SignificanceResult
from research_stats.significance import test_significance as _test_significance
```

The same aliasing pattern Components 3–5 already established
(`_run_backtest`, `_classify_regime`, `_compute_indicator`) — each new tool
function needs the bare name for itself, so its underlying implementation
gets imported under a leading-underscore alias. Applied identically to all
three new tools here (`_test_significance`, `_bootstrap_ci`,
`_correct_p_values`).

```python
@mcp.tool()
def confidence_interval(
    rule: StrategyRule,
    ticker: str,
    ...
) -> ConfidenceIntervalResult:
    ...
    result = _run_backtest(df, strategy_cls, ticker=ticker, **kwargs)
    return _bootstrap_ci(result.trade_returns, confidence_level=confidence_level, seed=seed)
```

This tool, not `research_stats/confidence.py`, is where "run a backtest,
get its trade returns, bootstrap a CI from them" actually gets composed —
deliberately, per section 3's asymmetry discussion. `bootstrap_ci` itself
never imports anything from `backtester` at all.

No new entries were added to `mcp_tools/schemas.py` for this component.
`SignificanceResult`, `ConfidenceIntervalResult`, and
`MultipleComparisonsResult` are real Pydantic models already defined in
`research_stats/`, imported and returned directly — the same precedent
Component 3 already set by returning `BacktestResult` (a `backtester/`-level
model) directly rather than wrapping it in a duplicate `mcp_tools`-only
schema.

---

## 3. Design decisions and rejected alternatives

### Refactoring `rule_strategy.py` — a bigger, more deliberate touch than Component 5's

Component 5 extracted one function (`compute_indicator_series`) from
already-committed Component 4 code, justified because three copies of real
computational logic would otherwise have existed. This component's
refactor is larger — four extracted functions, touching Stage 3 code
rather than this-same-stage code — and was explicitly discussed with the
user before being written, not decided unilaterally. The rejected
alternative was writing `make_random_entry_strategy` as a fully
self-contained function, duplicating `make_rule_strategy`'s indicator-wiring
and exit-handling logic rather than sharing it. That alternative would have
worked, in the narrow sense of producing correct backtests, but it would
have undermined the actual point of building a random-entry control at
all: the statistical comparison in `test_significance` is only valid
because the real strategy and its control are identical in every respect
except entry timing. Two independently-written, merely-similar-looking
implementations of "wire up the exit indicators and apply the exit
condition" would be a standing risk that a future edit to one accidentally
drifts from the other — at which point the "control" would no longer
actually be controlling for what it claims to control for, silently. Four
shared functions, called identically by both `make_rule_strategy` and
`make_random_entry_strategy`, make that drift structurally impossible
rather than merely unlikely.

**Reversibility:** the extraction is reversible in the narrow sense of
being able to inline the helpers back — but the *guarantee* it provides
(the two strategy types can't silently diverge in their shared exit logic)
would be lost the moment they were un-shared, which is the actual reason
this decision matters, not the code organization itself.

### The zero-trade retry mechanism — verified as a real risk before being designed around

Before writing any of `test_significance`, a `NeverEnter` strategy (a
throwaway `Strategy` subclass that places no trades at all) was run through
the real `run_backtest` to check what happens to a zero-trade result. It
returned `sharpe_ratio: nan` — a genuine Python `float`, not an exception.
That mattered directly: a `NaN` silently entering the null distribution
`monte_carlo_test` builds would corrupt its comparison, since `numpy`
comparisons involving `NaN` are always `False` — a `NaN` draw would neither
count as "at least as extreme as the observed value" nor be visibly
excluded, silently biasing the p-value in an unpredictable direction with
no error or warning to flag it.

The rejected alternative was filtering `NaN` values out of the null
distribution *after* `monte_carlo_test` had already consumed them — letting
occasional zero-trade draws happen, then cleaning up downstream. That was
set aside because `monte_carlo_test` computes the p-value internally, using
the null distribution it built from `rvs`'s return value directly; there is
no post-hoc opportunity to remove a bad draw once it's already been fed in
without either bypassing `monte_carlo_test`'s own p-value computation
entirely or accepting the corrupted result. Retrying *before* a bad draw is
ever handed to `monte_carlo_test` — with a capped, seed-advancing retry
loop that fails loudly rather than looping forever — was the only approach
that keeps `monte_carlo_test`'s own well-tested comparison logic
trustworthy rather than working around it.

**Whether this is a bias, and why it isn't:** conditioning the null
distribution on "produced at least one trade" could sound like tampering
with the randomness being modeled. It isn't, for a specific reason: a
Sharpe ratio is mathematically undefined for a return series with no
returns in it. There is no coherent way to compare a well-defined observed
Sharpe against a distribution that sometimes isn't a number at all — the
retry logic doesn't change what's being tested, it's the minimum condition
required for the test to be asking a meaningful question in the first
place.

### `n_resamples = 300`, and being honest about how that number was actually chosen

The approved Stage 4 plan's own Decision 1 text said "default N≈999,
tunable" — a round number chosen before any real cost had been measured.
Before writing this component's real code, one real backtest was timed on
full AAPL history: ~93ms. At `n_resamples=999`, that projects to roughly 93
seconds for a full-history call — not unreasonable given Stage 5's studies
are meant to run as background jobs (architecture.md §5 Step 4's "async,
not delayed"), but slower than felt right to accept without examining it.
`300` was chosen instead, explicitly discussed with and approved by the
user before being implemented, projecting to roughly 28 seconds — still
comfortable p-value resolution (~0.0033, well below standard 0.05/0.01
significance thresholds) at roughly a third of the original guess's
runtime.

What actually happened once the real code existed is worth stating
plainly, because it's a genuinely different number than either estimate:
the real, complete `test_significance` call with `n_resamples=300` against
real AAPL 2015–2024 data took **4.7 seconds** — not 28. The isolated
single-backtest measurement that produced the 93ms figure apparently didn't
reflect the true amortized cost of many backtests run in sequence (possibly
some form of warm-up cost in that first isolated measurement, though the
exact mechanism wasn't investigated further, since the direction of the
surprise — faster than expected, not slower — didn't create any correctness
risk worth chasing down). This is disclosed as a pleasant surprise, not
claimed as an accurately predicted outcome — the honest record is "the
plan's number was revised once based on a real measurement, and the real
end-to-end behavior turned out faster than even the revised estimate,"
not "300 was correctly calculated in advance."

### The `significance.py`/`confidence.py` asymmetry — genuinely different dependencies, not an inconsistency

`test_significance` takes `rule` and `price_data` directly and runs real
backtests internally. `bootstrap_ci` takes a plain `Sequence[float]` and
has never heard of `StrategyRule` or `backtesting.py` at all. This wasn't
an arbitrary split — it reflects a real difference in what each statistical
procedure actually needs. `test_significance` has no way to avoid running
backtests: the entire null distribution it needs *is* a collection of
random-control backtest results, so there was never a version of this
function that doesn't orchestrate `backtester` internals. `bootstrap_ci`,
by contrast, only ever needs a list of already-computed numbers — nothing
about resampling a list of floats and recomputing a statistic on each
resample requires knowing where those floats came from. The rejected
alternative — giving `bootstrap_ci` the same `rule`+`price_data` signature
as `test_significance`, for interface consistency — was set aside because
it would have made a genuinely generic, reusable function falsely appear to
depend on backtesting machinery it doesn't need, and would have meant
re-running a backtest inside `bootstrap_ci` itself even when a caller (the
`confidence_interval` MCP tool) already has the trade returns in hand from
a backtest it just ran for another reason.

---

## 4. Concepts introduced

**Monte Carlo hypothesis testing, and why "no distributional assumption"
is the actual selling point, not a side benefit.** A classical significance
test (a t-test, say) computes a p-value from a formula that assumes the
data follows some known distribution shape. A Monte Carlo test instead
*simulates* the null hypothesis directly — here, by literally running
hundreds of random-entry backtests — and asks what fraction of those
simulated outcomes are at least as extreme as what was actually observed.
The p-value's validity comes entirely from the simulation being a faithful
model of "what would happen under the null hypothesis," not from any
assumption about what shape the underlying returns take. This is exactly
why it was chosen over a t-test in the approved plan's Decision 1 (trading
returns are neither normally distributed nor independent, both assumptions
a t-test needs) — this component is where that decision actually becomes
executable code rather than a written justification.

**A null distribution's shape carries information a bare p-value
discards.** `SignificanceResult` reports `null_mean_sharpe` and
`null_std_sharpe` alongside `p_value`, specifically so a caller can see,
for example, that a p-value near 0.33 corresponds to a null distribution
whose mean (≈0.58) sits close to the observed value (0.68) — a strategy
whose real returns look a lot like what random entries on the same
trending stock would also produce, not a strategy that's "almost
significant" in some more dramatic sense a bare p-value alone wouldn't
distinguish from other ways of failing to reach significance.

**Trade-level resampling versus daily-bar resampling, made concrete.** The
approved plan's Decision 1 argued for trade-level bootstrap resampling
because trades from a long-only single-position strategy don't overlap in
time, unlike adjacent daily returns. This component is where that argument
became a real number: 44 real trade returns, not 2,486 daily bar returns,
went into `bootstrap_ci` — a visibly different, much smaller sample, and
the actual reason `BacktestResult` needed a new field at all.

---

## 5. How this component was tested

The most extensive verification of any component this stage, reflecting
both its size and one genuine bug it surfaced.

**Before any of this component's real code was written:** `scipy.stats.monte_carlo_test`,
`scipy.stats.bootstrap`, and `scipy.stats.false_discovery_control` were
each checked directly against toy examples to confirm their real calling
conventions, none of which had been used in this project before this
component. A `NeverEnter` strategy was run through the real `run_backtest`
to confirm the zero-trade `NaN` behavior discussed in section 3. A single
real backtest was timed to inform the `n_resamples` decision.

**A real bug, found by the first genuine test, not an offline check.** The
offline `monte_carlo_test` toy check used a throwaway `rvs` that called
`rng.normal(0, 1, size=size)` — which accepts both a plain int and a tuple
for `size` without complaint, so it never revealed that `scipy` actually
passes `size` as a tuple (confirmed directly: `(5, 1)` for a 5-resample,
length-1-sample call). The first real test of the actual `test_significance`
function — not a toy stand-in — failed immediately: `'tuple' object cannot
be interpreted as an integer`, from `range(size)` inside the real `rvs`.
This is a genuinely important nuance about verification itself, not just a
bug report: the earlier offline check *appeared* to verify the exact
mechanism that later broke, and technically exercised the real `scipy`
function correctly — but the specific code being verified (a
tuple-tolerant `numpy` call) was more permissive than the code that would
actually depend on the result (a tuple-intolerant `range()` call), so
passing that check provided less assurance than it looked like it did. The
fix — extracting a scalar count from `size` regardless of its shape, and
reshaping the return value to match `size` exactly — was verified
immediately afterward against the real function, not just reasoned about.

**Full end-to-end verification, against real AAPL data, through the actual
protocol handler:**

`run_backtest` was re-run with the identical known call from Component 3's
own verification (`SMA_CROSSOVER`, AAPL, 2015–2024) to confirm
`trade_returns` now carries 44 real values, matching `num_trades` exactly.

`test_significance` was run twice — first with `n_resamples=50` as a fast
smoke test, then with the real default of 300. The full run took 4.7
seconds (see section 3's honest account of that number) and returned
`observed_sharpe=0.678` (matching Component 3's own known result exactly),
`observed_num_trades=44`, `p_value≈0.326`, `null_mean_sharpe≈0.58`,
`null_std_sharpe≈0.19`. This is a real, defensible statistical finding, not
just evidence the code runs without crashing: a simple SMA crossover on
AAPL's unusually strong 2015–2024 uptrend does *not* convincingly beat
randomized entries sharing the same exit rule, because random entries on a
strongly trending stock also tend to produce a healthy Sharpe ratio from
the underlying drift alone — a textbook-consistent result for exactly the
kind of strategy and ticker combination this project's own Stage 3 gate
already flagged as sitting in an unusually persistent uptrend.

`confidence_interval` on the same real backtest returned a mean trade
return of ≈3.9%, 95% CI `[0.93%, 8.77%]`, `n=44` — sensible, real numbers.

`correct_p_values` on a toy list `[0.001, 0.01, 0.03, 0.04, 0.049, 0.2,
0.5]` produced correctly monotonic Benjamini-Hochberg-adjusted values.

Two error paths were verified, one of which required a second, more
careful attempt to actually reach the intended code path. `confidence_interval`
on a short AAPL window (2024-01-01 to 2024-06-01, one real trade) correctly
returned `is_error=True` with `"bootstrap_ci needs at least 2 values, got
1"` on the first attempt. `test_significance`'s zero-trades path did not:
the first attempt (a 7-day window) failed with a *different*, earlier
error — `Indicator "_compute(C,10)" error` — because `SMA(30)` cannot even
compute on 7 days of data, a data-length problem distinct from "the rule
structurally never fires." To actually verify the intended zero-trades
message, a deliberately impossible rule was constructed — `RSI` (bounded
`[0, 100]` by construction) with an entry condition of `crosses_below
-100`, structurally unreachable but computable on a normal-length window —
and confirmed the intended message fires correctly:
`"cannot test significance: 'impossible' produced 0 trades against this
data — no basis for a randomized-entry comparison"`.

**A general MCP-response-shape finding, not specific to this component but
first noticed here.** Reading `trade_returns` off `run_backtest`'s response
initially failed with `KeyError: 'result'` — the assumption, carried over
from Component 2 and 4's list-returning tools, was that every tool response
nests its payload under a `"result"` key. It doesn't: a tool returning a
single Pydantic model (not a list) spreads that model's fields directly at
the top level of `structured_content`. This applies retroactively to every
single-object-returning tool this stage has built — `run_backtest` since
Component 3, and now all three of this component's own tools — none of
which had this distinction explicitly noted before it caused a real,
if quickly diagnosed, failure here.

Full existing 170-test suite run three times across this component: once
as a baseline, once immediately after the `rule_strategy.py` refactor
alone (before `random_entry_strategy.py` or `research_stats/` existed, to
isolate whether the refactor itself changed anything), and once after the
complete component — unchanged all three times.

**What this does not prove.** No automated, committed test exists yet for
any of the three new tools or the two refactored/new `backtester` modules
— Component 8 is still where formal coverage lands, as with every
component this stage. The `confidence_interval` tool was only tested with
`np.mean` (its default `statistic`) — nothing verified a custom statistic
function actually works through the real MCP argument-coercion layer,
since function objects aren't natively JSON-serializable and no test
attempted to pass one. The retry-on-zero-trades logic's cap
(`_MAX_RETRIES_PER_DRAW=20`) was never actually exhausted in any test run —
its failure branch (the `raise ValueError` inside the `for...else`) is
untested code, present because it's the correct thing to do if the
scenario occurs, not because the scenario was ever produced and observed
failing correctly.

---

## 6. Interview defense

**Q: Why extract four helper functions from `rule_strategy.py` instead of
just writing `make_random_entry_strategy` as a smaller, self-contained
function that reimplements the parts it needs?**

A: Because "reimplements" is exactly the risk this design avoids. The
random-entry control's entire statistical purpose is to be identical to
the real strategy except for entry timing — if its exit logic and
indicator wiring were a second, independently-written copy, there would be
no guarantee the two stayed in sync as either was edited later. Sharing
the literal same functions makes that guarantee structural rather than a
matter of remembering to keep two copies consistent by hand.

**Q: Why is the significance test one-sided (`alternative="greater"`)
instead of two-sided?**

A: Because the question architecture.md actually poses is directional —
"did it *beat* randomized entries," not "is it *different from* randomized
entries in either direction." A two-sided test would also flag a strategy
that significantly *underperforms* random entries as "significant," which
isn't the claim this tool is built to support, and would make the p-value
harder to interpret against the specific question this project's own
design document asks.

**Q (hard): This component's own zero-trades verification admits the first
test attempt didn't actually reach the code path it was meant to test — it
hit `SMA(30)`'s own inability to compute on 7 days of data instead. Doesn't
that mean the "cannot test significance: produced 0 trades" error message
is functionally dead code for a large class of realistic short-window
requests, since they'd hit the indicator-computation failure first?**

A: For short enough windows, yes — and that's worth saying plainly rather
than implying the zero-trades message covers every zero-trade scenario. It
covers exactly one specific case: a rule whose indicators compute fine but
whose entry condition never structurally fires within the given data (the
`RSI crosses_below -100` case this component actually verified). A
different case — data too short for the rule's own indicators to compute
at all — surfaces a `backtesting.py`-native error instead, one this
component didn't write and doesn't control the wording of. Both are real
failure modes a caller could hit, and both correctly produce `is_error=True`
rather than a silent wrong result or a crash — but they're not the same
failure, and claiming this component's custom message "handles the
zero-trades case" without that distinction would overstate what was
actually built and verified.

**Q: The entry-probability calibration (`n_trades / len(data)`) doesn't
guarantee every simulated control actually produces `n_trades` trades —
some will have more, some fewer, and the retry logic only kicks in for the
zero-trades extreme. Isn't an *approximate* trade-frequency match a weaker
control than the architecture doc's phrase "the same trade frequency"
implies?**

A: The phrase describes the intent correctly; "exact" was never the right
reading of it. A null distribution built from controls that always hit
*exactly* `n_trades` trades, no more, no less, would be a narrower, more
artificial model of "random" than real randomness actually produces — real
independent trading decisions would naturally vary in how many happen to
land, run to run. Calibrating the *expected* count via a per-bar Bernoulli
probability, and letting individual draws vary around it, is the
statistically appropriate way to build this specific null distribution,
not a shortfall relative to an exact-match design that was never actually
the target.

**Honest weaknesses, stated plainly:** no committed automated test exists
yet for any part of this component, consistent with the rest of this
stage. The retry cap's failure branch has never actually been exercised
and observed to fail correctly — only reasoned through. `confidence_interval`
was only ever tested with its default statistic. And the zero-trades error
message's real scope, as covered above, is narrower than its wording alone
might suggest to a reader who hasn't seen this component's own verification
notes.

---

## 7. What comes next and why

Component 7 (screener tool) is the last of the six planned tools, and the
one requiring genuinely new data — broadening the ingested ticker universe
beyond the current 17, through Stage 1's existing retry/corporate-actions
pipeline rather than a shortcut (per the approved plan's Decision 2). It
has no direct dependency on this component's statistics machinery, but it
shares this component's general shape: real new domain logic, not a thin
wrapper, with its own real design questions (relative threshold cuts,
sensitivity testing) still to be resolved in its own explain-before-write
discussion.

If this component's core statistical machinery were subtly wrong in some
untested way, the most concerning failure mode wouldn't be a crash — it
would be a `p_value` that looks plausible but is quietly miscalibrated,
because the null distribution it was compared against wasn't actually a
faithful model of "random entries at the same frequency." That's precisely
the category of risk the zero-trade retry logic was built to prevent (a
`NaN`-contaminated null distribution would have produced exactly this kind
of silently-wrong-but-plausible-looking p-value), and precisely why this
component's own honest gaps — the untested retry-cap failure branch, the
narrower-than-implied zero-trades message — are worth carrying forward as
known residual risk rather than treating this component as fully closed
the moment its own tests happened to pass.
