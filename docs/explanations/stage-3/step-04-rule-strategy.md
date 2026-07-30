# Step 4 — The Strategy Interpreter (Stage 3)

## 1. What this does

`src/backtester/strategies/rule_strategy.py` is the Stage 3 payoff. Everything
built so far — the indicator registry, the rule schema, the pure evaluator — has
been inert with respect to real price data. `make_rule_strategy(rule) ->
type[Strategy]` is the function that closes that gap: given any validated
`StrategyRule`, it returns a real `backtesting.py` `Strategy` subclass, the
exact same kind of object Stage 2's `run_backtest` already knows how to run.
After this component, any of the four `KNOWN_STRATEGIES` — or any future rule —
can be backtested with zero strategy-specific Python code written per strategy.

This is also the component where two real, non-obvious bugs were found and
fixed by direct empirical investigation rather than by assumption, and where a
regression test written to guard against one of them was itself initially
wrong in a way worth documenting as carefully as the bug it was meant to catch.

---

## 2. Two things verified empirically before writing any code

Given the history of assumptions that turned out wrong in earlier components
(`bbands`'s `std` parameter, `stoch`'s `d=1` bug, VWAP's silent `None` — all
documented in [step-01-indicator-registry.md](step-01-indicator-registry.md)),
this component's design depended on two facts about `backtesting.py`'s runtime
behavior that were not safe to assume. Both were tested directly against a real
`Backtest` run before any of `rule_strategy.py` was written.

**Does `self.data` preserve a real `DatetimeIndex`?** `self.data.Close` turned
out to be `backtesting._util._Array` with no `.index` attribute at all — but it
exposes a `.s` property returning a genuine `pandas.Series` with the original
index intact. Calling `ta.vwap()` directly on the raw `_Array` objects
reproduced the exact Component 2 failure (`None`, no exception); calling it on
`.s`-derived Series worked correctly. The fix — build every indicator's price
arguments from `.s`, not just for VWAP but uniformly for all indicators — costs
nothing for the indicators that don't care about the index and removes an
entire class of "some future indicator might also silently need this" risk.

**Is `self.trades[-1].entry_bar` real, and does it mean what it sounds like?**
It exists, and it surfaced something a purely name-based assumption would have
missed: a hand-tracked entry-bar counter recorded `14` (the bar `self.buy()`
was called on), but the real `entry_bar` came back as `15`. This isn't a bug —
it's `backtesting.py`'s own next-bar-open fill semantics (the default
`trade_on_close=False`, already established in Stage 2): an order placed during
bar 14's `next()` fills at bar 15's open. `entry_bar` reports when the trade
*actually filled*, which is exactly what `exit_after_bars` bookkeeping needs. A
manually-tracked counter would have been silently off by one in precisely this
scenario, so it was dropped in favor of the library's own attribute.

A third check confirmed the load-bearing mechanics before they were relied on:
deep out-of-range negative indexing into both `self.data` and an
`self.I()`-wrapped indicator raises a clean `IndexError` (not silent garbage),
and `self.I()` correctly forwards `**kwargs` through to a function that returns
a `DataFrame` and needs column selection (tested with MACD).

---

## 3. `make_rule_strategy`'s structure

`make_rule_strategy` returns `type[Strategy]` — a class, not an instance —
because that's what Stage 2's `run_backtest`/`Backtest` already expect;
`Backtest` instantiates the strategy class internally. It's built as a plain
nested class closing over `rule`, the same shape Stage 2's `SMACrossover`
already established, just parameterized by data instead of hardcoded.

`_collect_indicator_terms`/`_collect_from_term` recursively walk a `Condition`
tree — through both sides of every `Comparison` and through `ScaledTerm.term`
— collecting every `IndicatorTerm` referenced anywhere. `make_rule_strategy`
runs this over `rule.entry` and `rule.exit` (guarding for `exit=None`,
`morning_star`'s case), concatenates the results, and folds them into
`unique_terms: dict[IndicatorKey, IndicatorTerm]` via `setdefault(
indicator_key(term), term)`. This is where deduplication actually happens —
and it's deliberately the *only* place it happens. Everything downstream
(*how* each unique indicator gets stored and read back) operates on the
already-deduplicated `unique_terms` dict, which is exactly why the storage bug
described in section 4 never touched dedup correctness itself — they're
separate concerns, and keeping them separate is why one could break without
taking the other down with it.

**Provenance** (`indicators_used`, `extended_indicators_used` — indicator
names split by `CORE_INDICATORS[name].tier`) is computed from `unique_terms`
and attached to the returned class via plain post-definition assignment
(`RuleStrategy.indicators_used = indicators_used`), not as a type-annotated
attribute inside the class body. This matters concretely: Stage 2 established
that `backtesting.py` treats annotated class-body attributes as tunable
strategy parameters, overridable via `bt.run()` — that's literally how
`SMACrossover.fast_period: int = 10` works. Provenance data must never be
mistaken for a tunable parameter by that mechanism, so it's attached after the
class body closes, at a point where it's structurally impossible for
`backtesting.py`'s parameter-scanning to see it as one.

---

## 4. `_normalize_params` — a real crash, from a completely reasonable earlier choice

The first real backtest run raised, immediately, inside `ta.sma`'s
numba-jitted internals: a `TypingError` on `length=10.0`, because numba's
compiled path requires a genuine Python `int` for an array-size parameter, not
a `float`. The root cause traces back to a decision made in Component 3, for
good reason at the time: `IndicatorTerm.params` is typed `dict[str, float]`
uniformly, so every param can be bounds-checked the same way regardless of
whether it's conceptually an integer bar-count or a genuinely fractional
multiplier. That choice was entirely correct *in isolation* — the problem only
exists once you actually try to call pandas-ta with the resulting values.

The fix, `_normalize_params`, casts any whole-valued float to `int` right
before the call (`length=10.0` becomes `10`). This was not assumed safe for
every param — it was checked. `ta.bbands(..., lower_std=1.0)` and
`ta.bbands(..., lower_std=1)` produce *different* column names
(`BBL_20_1.0_1.0` vs. `BBL_20_1_1`) but numerically *identical* values,
confirmed with `np.allclose`, not just `.equals()` (which would have failed on
the column-name mismatch alone and produced a misleading "these differ"
signal). Since `column_prefix` matching (Component 2) only ever matches by
prefix, never by exact column name, this cosmetic difference is invisible to
every downstream consumer. Casting whole-valued floats to `int` is safe for
every current core indicator, not just the length-style ones.

---

## 5. The dict-storage bug, in full — and the regression test that initially missed it

This is the central incident of this component, and it's worth walking
through in the order it actually happened, because the sequence matters as
much as the conclusion.

### The symptom

Every one of the four `KNOWN_STRATEGIES` ran without error, but
`sma_10_30_crossover` produced `num_trades=0`. That alone doesn't prove a bug
— maybe the synthetic data just doesn't cross. It was checked independently:
plain pandas, computing `SMA(10)`/`SMA(30)` rolling means on the *exact same*
500-bar dataset, showed 22 real sign changes. Zero trades against 22 known
crossing opportunities is unambiguous — this was a real defect, not an
artifact of the data.

### Isolating it

Rather than guess, `evaluate_condition` was temporarily monkeypatched at the
module level to print the SMA10/SMA30 values it was actually seeing, at
`next()` call #1, #100, #200, #300, #400. Every single call printed the exact
same four numbers — `sma10[-1]=114.900` at call #1 *and* at call #400 — while
`close[-1]` itself, checked the same way, correctly moved from roughly 97 to
112 over that same span. The indicator values were frozen; the price data
wasn't.

A minimal, targeted test narrowed this further: inside one `init()`, the same
`self.I()` return value (confirmed identical via `is`) was assigned to both a
named attribute (`self.sma_attr`) and a dict entry (`self._series["sma"]`).
At bars 35, 100, and 300, `self.sma_attr[-1]` correctly tracked price and its
`len()` grew (`45`, `110`, `310`) — while `self._series["sma"][-1]` stayed
frozen at `114.900` with `len()` constant at `500`, the full dataset, on
*the same object*.

### The actual mechanism, read from source rather than guessed

At this point the only way to resolve it honestly was to read
`backtesting.py`'s own code. `backtesting/_util.py`'s `_Array`/`_Indicator`
classes are plain `np.ndarray` subclasses — `__len__` is ordinary numpy array
length, with no built-in "current bar" awareness anywhere in the class body;
confirmed directly, not inferred.

The real mechanism lives in `Backtest.run()`'s simulation loop
(`backtesting/backtesting.py`). Immediately after `strategy.init()` returns,
`indicator_attrs = _strategy_indicators(strategy)` runs exactly once. Then,
for every bar of the simulation, *before* calling `strategy.next()`:

```python
for attr, indicator in indicator_attrs:
    setattr(strategy, attr, indicator[..., :i + 1])
```

`backtesting.py` physically **reassigns** every discovered attribute, every
bar, to a freshly length-truncated slice. `_strategy_indicators` itself is one
line: `{attr: indicator for attr, indicator in strategy.__dict__.items() if
isinstance(indicator, _Indicator)}.items()` — a one-time scan of the strategy
instance's own `__dict__` for values that are *directly* `_Indicator`
instances. `strategy.__dict__["_series"]` is a `dict`, and a `dict` is not an
`_Indicator` — it fails that `isinstance` check outright, regardless of what's
nested inside it. Anything stored only inside a container is invisible to
this scan and is never touched again after `init()`; every later read for the
rest of the entire backtest silently returns the full, `init()`-time
snapshot. There's also no naming-convention filter — confirmed by reading the
one-line implementation directly — so an underscore-prefixed attribute name
was never the problem, ruling out a simpler theory before committing to the
real fix.

### The fix

Each precomputed indicator is now stored as its own uniquely-named instance
attribute (`self._ind_0`, `self._ind_1`, ..., via `setattr`, enumerated over
`unique_terms`), with a separate, genuinely harmless dict (`self._key_to_attr:
dict[IndicatorKey, str]`) mapping each indicator's key to its attribute name.
That second dict is safe precisely because its *values* are plain strings,
never `_Indicator` instances — `_strategy_indicators`'s `isinstance` check has
nothing to reject there. `_RuleBarContext.indicator()` looks up the attribute
name, then calls `getattr(self._strategy, attr_name)` fresh on every
invocation — which is what makes it see whatever `backtesting.py` most
recently wrote to that name, rather than whatever was there at `init()` time.

### The regression test's own false negative

A test was written immediately after the fix, reusing one indicator (`RSI(14)`)
identically across both `entry` and `exit` — intended to prove dedup produces
exactly one attribute *and* to guard against this exact bug recurring. Its
first version collected `len(getattr(self, attr_name))` across every call to
`next()` and asserted the sequence was strictly increasing. It passed.

When asked to confirm the fix was solid with the same rigor already applied
elsewhere in Stage 3, the honest next step was to prove the *test* worked, not
just that it passed — by deliberately reintroducing the exact original bug
(temporarily reverting `_RuleBarContext.indicator()` to read from a
`self._series` dict, while leaving the correctly-updating named attribute in
place alongside it) and rerunning the test. **It still passed.** A false
negative, caught only because it was deliberately checked for rather than
assumed away.

The reason is precise, not incidental: the test's own instrumentation called
`getattr(self, attr_name)` *directly* — bypassing `_RuleBarContext.indicator()`
entirely. The named attribute is *always* correctly re-sliced by
`backtesting.py`'s loop, regardless of whether the exact same object is *also*
sitting inside a broken dict somewhere else; attribute discovery doesn't care
who else holds a reference. The first test measured "is `backtesting.py`'s own
slicing mechanism working" — a question that was never actually at risk — 
instead of "does *our* lookup code correctly use that mechanism," which is
where the real bug lived and where a useful test needed to look.

The fix: rewrite the test to call `self._ctx.indicator(rsi_key, offset=0)` —
the exact method `evaluate_condition` itself calls during real rule evaluation
— and assert the returned *value*, not a length, isn't frozen across calls
(RSI genuinely fluctuates bar to bar on real data, so a constant value is
diagnostic, not just suspicious). This version was proven both ways: run
against the reintroduced bug, it failed with a specific, on-topic message —

```
AssertionError: BarContext.indicator() returned the same RSI value on every
one of 498 calls to next() (value: 40.273316111966096). RSI genuinely
fluctuates bar to bar on this data — a frozen value here means the lookup is
reading a stale, full-length snapshot instead of the live, current-bar-sliced
array. This is exactly the dict-storage bug this test guards against.
```

— and, with the fix restored (and diffed byte-for-byte against a pre-edit
backup, to confirm the restoration was exact rather than reconstructed from
memory), it passed cleanly.

The general lesson, stated plainly because it generalizes well past this one
bug: a regression test can pass for a reason that has nothing to do with
whether the underlying bug is actually fixed, if it doesn't exercise the exact
code path the bug lives in. Proving a test *passes* against working code and
proving a test *catches* the bug it's named after are two different claims —
the second requires deliberately reintroducing the failure and watching the
test fail for the right reason. This is the same discipline the Stage 3 plan
already requires elsewhere (Component 7's extended-indicator verification is
explicitly required to reject a deliberately-mis-specified indicator, not just
accept correctly-specified ones) — this component is the first place that
exact discipline was shown to matter for a test that, on first write, didn't
yet have it.

---

## 6. `next()`

```python
def next(self) -> None:
    if not self.position:
        if evaluate_condition(rule.entry, self._ctx):
            self.buy()
        return
    if rule.exit is not None and evaluate_condition(rule.exit, self._ctx):
        self.position.close()
        return
    if rule.exit_after_bars is not None and self.trades:
        bars_held = (len(self.data) - 1) - self.trades[-1].entry_bar
        if bars_held >= rule.exit_after_bars:
            self.position.close()
```

Flat and unremarkable given everything upstream already works: evaluate entry
when flat, evaluate exit-condition-or-bars-held when in a position. The one
piece worth restating here rather than treating as boilerplate is
`self.trades[-1].entry_bar` — chosen specifically because section 2 proved it
more *correct* than a hand-tracked counter, not merely more concise. A manual
counter would have recorded the bar the order was *placed*, not the bar it
*filled*, and would have been silently off by one for every single
`exit_after_bars` rule under the default fill timing.

---

## 7. How the verification gate was satisfied

- All four `KNOWN_STRATEGIES` run through the real `run_backtest` (not a
  lower-level `Backtest` call) and produce trades.
  `sma_10_30_crossover: num_trades=11` — checked against independently
  computed manual pandas SMA crossings (22 sign changes) divided by two (one
  up-cross to enter, one down-cross to exit, per completed trade): 22 / 2 =
  11, an exact match, not just "nonzero."
- Indicator deduplication proven at two levels: the collection level
  (`_collect_indicator_terms` on entry+exit yields 2 raw `IndicatorTerm`s, 1
  unique key), and via runtime introspection on a live instance
  (`_key_to_attr` has exactly one entry; exactly one `_ind_0` attribute
  exists; `getattr()` from both the entry-side and exit-side key resolves to
  the identical object, confirmed via `is`).
- VWAP proven working end-to-end through the *real* `self.I()` path for the
  first time — closing the question first raised in Component 2 and revisited
  in Component 4's residual-risk section. A rule crossing price against VWAP
  produced 128 trades on synthetic data.
- The `bbands` fractional-param case (`lower_std=1.5`) confirmed still working
  after the int-normalization fix was added.
- `test_rule_strategy.py`'s regression test, and its full false-negative-then-
  fixed history described above.
- The full suite — 16 existing tests plus this new one — passes: 17/17.

**What this does not prove.** `BacktestResult` still has no
`indicators_used`/`extended_indicators_used` fields — this component computes
and exposes that data on the returned class, but nothing yet threads it into
the actual result object `run_backtest` returns; that's plan-item 5, a
separate, not-yet-built component. There is still no formal
`test_indicator_core.py`, `test_evaluator.py`, or `test_schema.py` — all
Component 8. And every verification in this document ran against synthetic,
seeded random-walk data — the Stage 3 gate script (real AAPL data,
literature-consistent result ranges) hasn't run yet, and real market data
could still surface something synthetic data doesn't, from index/frequency
quirks to a literature strategy's real-world trade frequency falling outside
the gate's expected bounds.

---

## 8. Interview defense

**Q: Why not just test through the public API (`self._ctx.indicator(...)`)
from the start — why did the first version of the test try to read the
attribute directly at all?**

A: It seemed like a *more* direct, lower-level check at the time — reading
`getattr(self, attr_name)` felt like it was verifying the storage mechanism
itself, closer to the metal, which intuitively felt more rigorous than going
through another layer of indirection. The actual lesson is closer to the
opposite: testing a lower-level implementation detail instead of the exact
path production code uses is a design mistake independent of this specific
bug, because a component can be entirely correct at the level you tested and
still wrong at the level that actually matters. "Closer to the metal" isn't
the same as "closer to what's true."

**Q: Why store the key-to-attribute-name mapping in a dict at all, if a dict
was literally the source of the bug? Isn't that the same mistake again?**

A: No, and the distinction is exactly the point of the fix: `self._key_to_attr`'s
*values* are plain Python strings, never `_Indicator` instances, so
`_strategy_indicators`'s `isinstance(v, _Indicator)` check has nothing to find
there regardless — that dict was never going to be scanned for live-updating
purposes, and it doesn't need to be, because it isn't storing the thing that
needs live updating. The actual indicator data lives only in named attributes;
the dict only ever stores where to look for it. Conflating "a dict is
involved" with "this is the bug" would be exactly the kind of pattern-matched,
unverified conclusion this whole investigation was trying to avoid.

**Q (hard): Your regression test initially gave you false confidence — it
passed cleanly against a real, deliberately reintroduced instance of the exact
bug it was supposed to catch. How do you know this current version isn't
making the same category of mistake in some other way? And more broadly, how
much of Stage 3's other "verified" claims should be trusted, given this one
wasn't as solid as it looked on first write?**

A: The honest answer for *this specific test* is that it's no longer resting
on inspection alone — it was proven against both states, not one. It fails
with a specific, on-topic message when the bug is present, and passes when the
bug is fixed, and both of those were actually run, not assumed. That two-sided
proof — reintroduce the failure, watch the test fail for the stated reason,
restore the fix, watch it pass — is the generalizable defense against exactly
this class of false confidence, and it's precisely what was missing the first
time. What it does *not* do is retroactively certify every other verification
claim made across Stage 3. Most of them — Component 2's registry findings,
Component 3's thirteen `ValidationError` cases, Component 4's NaN guards —
were established by directly triggering the specific failure and observing
the result, which is the same underlying spirit (evidence over assumption),
but this is the first place a *written, persisted* test was caught having
skipped that discipline on its first draft. The honest position is: the
one-off interactive checks throughout Stage 3 are as trustworthy as the
specific evidence shown for each of them, individually — this incident is a
reason to be more careful about newly-written persistent tests going forward,
not a reason to distrust everything that came before it, but it is a real
data point that "I wrote a test and it passed" is weaker evidence than it
feels like in the moment.

**Honest weakness:** this is the only test file in Stage 3 so far that exists
as a real, committed `pytest` test rather than an interactive smoke test — and
it's the one place that discipline was shown to need a second pass to get
right. That's a point in favor of writing tests earlier and more often, not
evidence the interactive-verification approach used for the rest of Stage 3
has been wrong; it's a reminder that persisted tests carry more weight and
therefore deserve more scrutiny before being trusted, exactly because they're
the artifact that keeps making claims on every future change without anyone
re-checking them by hand.

---

## 9. What comes next and why

Plan-item 5, "`BacktestResult` provenance fields," threads
`indicators_used`/`extended_indicators_used` — already computed and exposed
by `RuleStrategy` in this component — into the actual `BacktestResult`
Pydantic model `run_backtest` returns, so a study's result can disclose which
indicators it depended on and whether any came from the (still nonexistent)
unverified extended tier. This component deliberately stops short of touching
`result.py` itself, since that's separately scoped, not because the data isn't
ready.

The next point where something could still go wrong that this component's
synthetic-data verification wouldn't catch is the Stage 3 gate script itself —
real AAPL data, checked against literature-consistent ranges. Everything
proven here used a seeded random walk; real market data has its own index and
frequency characteristics, and a literature strategy's real-world trade count
or Sharpe ratio could fall outside the gate's expected bounds for reasons that
have nothing to do with whether this component's logic is correct. If that
happens, this component's own verification — thorough as it was — won't be
the place that explains why; it'll be the first real test of whether the
whole Stage 3 pipeline, built and verified end to end on synthetic data,
actually holds up against the real thing.
