# Step 8 — Formal Test Suite (Stage 4)

## 1. What this does

This component gives every one of Stage 4's six tools its first automated,
committed, repeatable test coverage — `tests/backtester/test_indicator_compute.py`,
`test_regime.py`, `test_random_entry_strategy.py`, extensions to
`test_rule_strategy.py`, a new `tests/research_stats/` package
(`test_significance.py`, `test_confidence.py`, `test_multiple_comparisons.py`),
and `tests/data_pipeline/test_screener.py`/`test_universe.py`. Every
component from Component 2 through Component 7 disclosed the identical gap
in its own step explainer's "what this does not prove" section: real,
interactive verification against real data, but nothing a future change
could silently break without a human noticing. This component closes that
gap for all six tools in one formal pass, following the same discipline
Stage 3 closed with — a test that passes on the first try without ever
having been checked to fail correctly is not yet trusted.

What this component became, beyond its planned scope: writing a genuine,
meaningful test for Component 6's trade-count calibration claim — not
just re-testing what Component 6 already claimed to be true, but actually
checking it — surfaced a real, substantive defect in already-shipped,
already-approved code. Section 3 covers this in full. This is not a
digression from Component 8's purpose; it is that purpose working exactly
as intended. A formal test suite that only re-confirms what interactive
testing already showed would be much less valuable than one that finds
what interactive testing missed.

---

## 2. Every meaningful line explained

Given the scale of this component (nine test files, roughly fifty new
test functions), this section focuses on the code that changed
production behavior — the calibration fix — rather than walking every
test function individually. The tests themselves are written to be
self-explanatory via their own docstrings, following the same "why this
assertion, not just what it checks" standard every prior component's
tests in this project already use.

### The fixture-scoping fix, found before any calibration work began

```python
# tests/conftest.py (root) — moved here from tests/backtester/conftest.py
def make_synthetic_data(n_bars: int = 500, seed: int = 42) -> pd.DataFrame:
    ...

@pytest.fixture
def synthetic_data():
    return make_synthetic_data()
```

The first new test file (`tests/research_stats/test_significance.py`)
failed at collection with `fixture 'synthetic_data' not found` — not a
code bug, a real property of how pytest's `conftest.py` inheritance works:
a directory's fixtures are visible to itself and its subdirectories only,
never to sibling directories. `tests/research_stats/` is a sibling of
`tests/backtester/`, not a descendant of it, so a fixture defined only in
`tests/backtester/conftest.py` was never going to be visible there no
matter how the test itself was written. The root `tests/conftest.py`'s own
docstring already anticipated this exact situation ("visible to
tests/data_pipeline/, tests/backtester/, and any future test package") —
`tests/research_stats/` is that future package, arriving for the first
time in this exact component. `make_synthetic_data`/`synthetic_data` moved
up to the root; `synthetic_to_db_df`/`seeded_db` (genuinely
backtester-DB-specific, needed nowhere else) stayed where they were.

### The pytest name-collision fix

```python
from research_stats.significance import test_significance as run_significance_test
```

A second, unrelated setup problem, also found at collection time: pytest
discovers test functions by name pattern (`test_*`), including names it
finds via a plain `import` statement, not just ones actually defined in
the file. `from research_stats.significance import test_significance`
made pytest treat the *imported* function — with its real signature
(`price_data`, `rule`, `ticker`, ...) — as an additional test case in this
file, which pytest then tried to call with no matching fixtures, producing
`fixture 'price_data' not found`. Aliasing the import on the way in avoids
the collision; every call site in the file uses the alias.

### The calibration bug — investigated per an explicit, deliberate process before any fix code was written

The approved Stage 4 plan's Decision 1 committed to `entry_prob = n_trades
/ len(data)` for the random-entry control's calibration, with the explicit
caveat that it targets `n_trades` "in expectation, not an exact per-draw
guarantee." Writing a real test for that specific claim —
`test_expected_trade_count_is_approximately_calibrated`, checking the mean
realized trade count across 30 seeds against the target — failed
immediately: a target of 40 realized a mean of 10.1, not "approximately
40 with natural variation."

Rather than loosen the test's tolerance until it passed (which would have
hidden the finding, not resolved it) or assume the discrepancy was a fluke
of one target value, the actual mechanism was investigated directly, with
real data, before any fix was designed:

```
target=  5  mean_realized=3.6   ratio=0.73
target= 10  mean_realized=6.3   ratio=0.63
target= 20  mean_realized=8.2   ratio=0.41
target= 40  mean_realized=10.0  ratio=0.25
target= 80  mean_realized=10.9  ratio=0.14
```

The realized count *saturates* near 10–11 regardless of how high the
target climbs — pushing entry probability higher stops helping past a
point. The actual cause, confirmed by computing the raw exit-signal
calendar directly (every bar where `SMA(10)` crosses below `SMA(30)`,
independent of whether any strategy happened to be in a position at that
bar): there are only **10** such events in the entire 500-bar series, with
gaps between them ranging 15–80 bars. Every realized trade — real or
random-control — must close at one of those sparse, fixed historical
events. Once entry probability is high enough that a position is almost
always open, the trade count is bounded by how many such events exist in
the data, a ceiling no amount of additional entry probability can raise.
A direct comparison of a saturated random control's actual holding
periods (8–71 bars, mean 35.7) against the raw exit-signal gaps (15–80
bars, mean 44) confirmed this was the mechanism — not a few catastrophic
outlier-long trades eating the data, which would have shown a very
different, much more skewed holding-period distribution.

This also confirmed, as a direct consequence of the same mechanism, that
`exit_after_bars`-only rules were never actually affected: their exit
timing is fixed and data-independent (any entry gets a guaranteed
same-length hold), so there's no sparse calendar to saturate against. A
parallel measurement for exactly this rule shape confirmed much better
(though not perfect) calibration: ratio 0.88 at target 10, degrading only
to 0.67 at target 40 — real variation, not the severe collapse the
data-dependent case showed.

### `backtester/result.py` — a third provenance field

```python
exit_bars: list[int] = Field(default_factory=list)
```

Populated from `stats["_trades"]["ExitBar"].tolist()`, the same column
`trade_returns` already reads `ReturnPct` from. The fix needs the real
strategy's own historical exit bar positions as fixed anchors — nothing in
the codebase exposed them before this addition. This is the third field
added to `BacktestResult` for exactly this reason
(`indicators_used`/`extended_indicators_used` in Stage 3, `trade_returns`
in this same stage's Component 6) — provenance that starts as "the data
was already there, why not keep it" and ends up load-bearing.

### `backtester/strategies/random_entry_strategy.py` — the anchored control

```python
def make_anchored_random_entry_strategy(rule: StrategyRule, exit_bars: list[int], seed: int) -> type[Strategy]:
    ...
    sorted_exits = sorted(exit_bars)

    class AnchoredRandomEntryStrategy(Strategy):
        def init(self) -> None:
            rng = np.random.default_rng(seed)
            self._entry_bars: set[int] = set()
            self._exit_signal_bars: set[int] = set()
            prev_exit = -1
            for exit_fill_bar in sorted_exits:
                low = prev_exit + 1
                high = exit_fill_bar - 2
                if high >= low:
                    entry_signal_bar = int(rng.integers(low, high + 1))
                    self._entry_bars.add(entry_signal_bar)
                    self._exit_signal_bars.add(exit_fill_bar - 1)
                prev_exit = exit_fill_bar

        def next(self) -> None:
            current_bar = len(self.data) - 1
            if not self.position:
                if current_bar in self._entry_bars:
                    self.buy()
                return
            if current_bar in self._exit_signal_bars:
                self.position.close()
```

One randomized entry paired with each real historical exit, guaranteeing
the trade count by construction rather than by chance. The full pairing
is precomputed once in `init()` — this strategy never evaluates
`rule.exit`'s condition at all; it doesn't need to, since `exit_bars`
already *is* that condition's real, already-computed historical effect.

`high = exit_fill_bar - 2` is the load-bearing bar-arithmetic detail,
derived from (and confirmed against) the exact fill-timing mechanic
Component 8 also had to pin down precisely for the `exit_after_bars`
off-by-one below: both entry and exit orders in `backtesting.py` fill one
bar *after* the `next()` call that signals them. `exit_bars[i]` is
already a *fill* bar (as recorded in `backtesting.py`'s own trades table),
so making a control's exit fill at that same bar requires its close
signal to fire one bar earlier, at `exit_bars[i] - 1`. The entry signal
must fire early enough that its own fill (`entry_signal_bar + 1`) still
lands strictly before that close signal — `entry_signal_bar <=
exit_fill_bar - 2` is exactly that constraint, not an arbitrary buffer.

`if high >= low:` is the tight-gap guard, discussed and confirmed as the
right approach before it was written (see section 3): when two
consecutive real exits are too close together to fit any valid,
non-overlapping entry window, that anchor is skipped rather than forced.

### `research_stats/significance.py` — dispatch between the two mechanisms

```python
def make_control(seed_value: int) -> type:
    if rule.exit is not None:
        return make_anchored_random_entry_strategy(rule, observed.exit_bars, seed=seed_value)
    return make_random_entry_strategy(rule, observed.num_trades, seed=seed_value)
```

The one place `test_significance` decides which mechanism to use, based
on the exact property that determines which one is actually correct —
whether `rule.exit` is a data-dependent condition (sparse calendar, needs
anchoring) or `None` (fixed-length `exit_after_bars` only, the
probability approach was never broken for it).

```python
null_mean_trades: float
null_std_trades: float
```

Two new fields on `SignificanceResult`, populated from every control's own
realized `num_trades` across the resampling loop. Not strictly required by
the fix itself (the anchored mechanism's guarantee doesn't depend on a
caller checking it), but added so the guarantee is *empirically visible*
in every call's own output — a caller can confirm the null distribution's
trade count actually matched the real strategy's, rather than trusting an
unstated internal property. Directly verified against real AAPL data:
`null_mean_trades: 43.99`, `null_std_trades: 0.099` against an
`observed_num_trades` of 44 — the guarantee holding almost exactly, with
the tiny residual variance being real (a handful of resamples drawing
entries at the very edges of their valid windows) rather than a sign of
imprecision.

---

## 3. Design decisions and rejected alternatives

### Investigate the real mechanism before designing any fix — a deliberate, requested process, not incidental

Before any fix was proposed, the actual cause of the saturation was
confirmed directly: the raw exit-signal calendar (10 events, real gaps
measured), and a direct comparison between the saturated control's actual
holding-period distribution and that calendar's own gap distribution —
specifically to distinguish "a few outlier-long trades are eating the
data" from "the real ceiling is a hard, structural constraint on how many
trades can exist at all." This distinction determined which fix would
actually be correct: a few outlier trades would have suggested some kind
of holding-period cap or retry-on-long-hold logic; a hard structural
ceiling on available exit events meant the only real fix was to stop
leaving trade count to chance at all. The rejected alternative — designing
a fix from the saturation numbers alone, without confirming which
mechanism produced them — was explicitly avoided, because two genuinely
different-looking symptoms (saturation from rare catastrophic holds vs.
saturation from a hard event-count ceiling) would have called for two
different, non-interchangeable fixes.

### Anchored pairing over recalibrating the probability formula

A tempting, smaller alternative existed: keep the probability-based
mechanism, but calibrate `entry_prob` empirically (run a few probe
backtests first, adjust the probability until the realized count is
close enough to the target, then use that calibrated probability for the
real resampling loop). This was not pursued, for a reason grounded
directly in the measured saturation curve: at `target=80`, the realized
count only reached 10.9 no matter how high entry probability climbed —
there is no probability value, calibrated or not, that makes a
probability-based mechanism produce more trades than there are sparse
exit events to close them at. Empirical calibration would have improved
accuracy for *moderate* targets relative to a rule's own event count, but
it could never actually solve the problem for a target that exceeds that
ceiling — which is exactly the regime real usage sits in (44 trades
against AAPL's own real exit-event count, not a small target chosen to
stay comfortably under an unknown ceiling). Anchoring directly to the
real historical exit bars sidesteps the ceiling entirely, because it never
tries to *produce* more trade opportunities than exist — it uses exactly
as many as the real strategy itself found, no more, no fewer (except the
documented tight-gap exception).

### The tight-gap skip, not a forced or approximated entry

When a historical gap between two consecutive real exits is too short to
fit a valid, non-overlapping entry window, the anchor is skipped —
producing one fewer trade for that specific draw — rather than either (a)
forcing an entry into an invalid or overlapping window, which would
corrupt the backtest's own exclusive-position invariant, or (b)
approximating with some other placement rule (e.g., always entering
immediately after the previous exit, regardless of whether that leaves
enough room before the next one). Both rejected alternatives would trade
a rare, honestly-disclosed shortfall for a subtler, harder-to-notice
correctness problem. On real daily-bar data this is expected to be a rare
event — the shortest real gap measured in this component's own
investigation was 15 bars — but it was verified directly rather than
assumed away: a deliberately pathological `exit_bars` list (three
consecutive bars one apart, then one normal gap) was fed to
`make_anchored_random_entry_strategy` directly, confirming it produces
fewer trades than anchors (the skip firing correctly) without crashing or
producing an invalid, overlapping position.

### `null_mean_trades`/`null_std_trades` disclosed even though the guarantee is now structural

Once the anchored mechanism guarantees the trade count by construction,
disclosing the achieved count in every response might look redundant —
why report a number that should always match by design? The reasoning
for keeping it: "by design" is a claim about the code, not something a
caller of the tool can see without either reading the source or being
told. Reporting it turns an internal guarantee into something empirically
checkable per call, the same "measure and disclose" instinct this project
has applied repeatedly this stage (the screener's `group_size`, the regime
classifier's `insufficient_history` label) — here applied to a guarantee
about the tool's own correctness, not a property of the input data.

---

## 4. Concepts introduced

**A null distribution's own internal validity, not just its existence.**
Every component before this one treated "build a null distribution by
running many random-control backtests" as a solved design question, closed
in Component 6. This component is where that assumption was actually
tested rather than re-asserted — and where "the mechanism runs without
error and produces a distribution" turned out to be a much weaker claim
than "the distribution it produces is actually a faithful model of what it
claims to represent." A Monte Carlo test's p-value is only as trustworthy
as the null distribution feeding it; a subtly mis-specified null (here,
one built from systematically under-traded, longer-held random controls)
doesn't fail loudly — it produces a p-value that looks completely
ordinary, differing from the correct one only in being wrong.

**Saturation versus natural variation, as genuinely different failure
shapes.** The approved plan's own language — "expected trade count
approximates the target, not an exact per-draw guarantee" — correctly
anticipated natural variation as expected and acceptable. What it didn't
anticipate, and what this component's own measurement surfaced, is a
qualitatively different failure: not variation *around* a correctly
centered target, but a hard ceiling the mean itself can never approach
past a certain target size, regardless of how many draws are averaged.
Distinguishing these two matters because only one of them is fixable by
"average over more draws" or "accept wider variance" — the other requires
recognizing that the target was never reachable by the mechanism at all.

---

## 5. How this component was tested

**The formal suite itself**, across all six tools: 220 tests total (from
170 before this component), covering indicator computation (cross-checked
against independent pandas computations), regime classification (including
a direct lookahead-safety test — truncating data after a given bar and
confirming that bar's own classification doesn't change, the same
discipline Sacred Gate 1 requires of the backtester, applied here for the
first time to a non-backtesting component), the random-entry
mechanisms (both variants), the three `research_stats` functions
(including a regression test that would fail immediately if the
tuple-vs-int `size` bug from Component 6 ever resurfaced), and the
screener (including a deterministic, synthetic version of the `as_of`
point-in-time proof — a ticker with a known, controlled volatility change
at a known date, stronger than relying on real historical market behavior
having happened to hold true).

**The calibration fix, verified twice** — once against the synthetic
500-bar fixture (`make_anchored_random_entry_strategy` on
`sma_10_30_crossover`, confirming exactly `len(exit_bars)` trades, exit
bars matching the real strategy's own exactly, entries varying across
seeds while exits stay fixed), and once against real AAPL data through
the actual MCP protocol handler — the same call Component 6's own original
verification used. The real-data result is the one that matters most:
`null_mean_trades: 43.99` against `observed_num_trades: 44` (the
guarantee holding almost exactly), and a p-value of 0.0099 — a
*qualitatively different conclusion* from Component 6's own original
0.33, not merely a more precise version of the same one.

**What this does not prove.** The tight-gap skip's correctness was
verified with a synthetic, deliberately pathological example, not a real
one — no real `KNOWN_STRATEGIES` rule on real data has actually been found
to trigger it, so its behavior on genuinely real (rather than
constructed) tight-gap data remains unexercised. The `exit_after_bars`-only
calibration's own remaining imprecision (ratio 0.67 at target 40, not
saturating catastrophically but not exact either) is disclosed in this
component's own test tolerance but not further investigated — it's
possible a similar, smaller-magnitude version of Component 6's original
problem exists there too, just not severe enough to have been caught by
this component's own (deliberately generous) tolerance. And this
component's tests, like every one before it this stage, have not been
run against the manual MCP protocol layer at all — that remains
Component 9's job, not this one's.

---

## 6. Interview defense

**Q: Why didn't Component 6's own verification catch this, given it ran a
real test_significance call against real AAPL data and reported real
numbers?**

A: Because Component 6's verification checked that the *mechanism ran and
produced plausible-looking output* — a real p-value, a real null
distribution with a sensible mean and standard deviation — not that the
null distribution's own construction actually matched its stated design
intent (the same trade frequency as the real strategy). Nothing in that
verification compared the controls' own realized trade counts against 44;
the number simply wasn't checked, because nothing in that component's own
test plan asked the specific question this component's test did. This is
exactly why a formal, adversarial test suite is a different, complementary
kind of verification from interactive spot-checking — the interactive
check confirms a person's specific expectations were met; a real test,
written to check a documented claim rather than confirm an impression, can
catch what the person checking didn't think to ask.

**Q: Why is this a Component 8 finding rather than something that should
have blocked Component 6 from shipping in the first place?**

A: Because at the point Component 6 shipped, "expected trade count
approximates the target" was a reasonable, honestly-stated design claim
that hadn't yet been checked against real, adversarial numbers — the
saturation curve in section 2 didn't exist as a known fact until this
component's own investigation produced it. This is the same shape of
answer this project has given before for a disclosed limitation later
found to matter more than expected (Stage 3's `rsi_14_30_70` deviation,
found and reproduced independently rather than assumed away): the honest
answer isn't "it should have been caught earlier" in the abstract, it's
"here is exactly when it was actually caught, and exactly what closed the
gap between assumption and verification."

**Q (hard): `SignificanceResult.p_value` changed from ≈0.33 to ≈0.0099 for
the exact same real strategy, ticker, and date range this project has
already used as its own worked example in multiple explainers. If this
tool had already been used to support a real research verdict before this
fix, that verdict's headline number would have been wrong — not
imprecise, wrong in the direction of understating significance. How do you
defend having shipped a tool capable of that?**

A: By being exact about what "shipped" means here: this tool has not yet
been used by anything downstream — no Stage 5 agent exists yet to have
consumed a `test_significance` call and acted on it, and every actual use
of it so far, including the one now-superseded 0.33 result, was this
project's own verification exercise, not a real research finding presented
to anyone as a conclusion. The honest defense isn't "no harm was possible"
in some absolute sense — a tool with this defect, used in Stage 5 before
this fix landed, would have systematically understated significance for
every rule with a data-dependent exit condition, which is a real and
serious category of error to have shipped uncaught. The actual defense is
that this project's own build order — six deterministic tools, formally
tested, before any agent exists to rely on them — is specifically designed
so that a defect like this gets found and fixed at exactly this point,
before it could reach a real decision, not after. This component finding
it here, before Stage 5, is the build order working as intended, not a
lucky near-miss.

**Honest weaknesses, stated plainly:** the tight-gap skip's real-world
behavior is unverified on genuine (not constructed) data, as covered in
section 5. The `exit_after_bars`-only calibration path's own residual
imprecision was measured but not further investigated — a smaller version
of the same class of problem may exist there, undiscovered, because this
component's test tolerance for that case was set generously enough not to
surface it if it does. And this entire fix — design, code, and tests —
happened inside what was originally scoped as "write tests for
already-correct code," a genuine scope expansion mid-component; the
formal test suite's other five tools received comparatively less
individual scrutiny in this same pass than the one that turned out to
need it most, simply because nothing about their own designs happened to
raise the same red flag when their tests were written.

---

## 7. What comes next and why

Component 9 — manual MCP verification — is the actual, literal Stage 4
gate ("call each manually through MCP before any agent touches them," per
`docs/architecture.md`'s own stated criterion). Every tool this stage has
built has been verified interactively through the real protocol handler
already, component by component, as each was written — Component 9's job
is to do that same verification systematically and completely, across all
six tools, happy path and invalid input alike, in one pass, rather than
distributed across seven different components' own individual
verification sections as it has been so far.

If this component's own calibration fix turns out to have a residual,
undiscovered problem — the tight-gap skip's untested real-data behavior
being the most concrete candidate — the most likely symptom, consistent
with everything this stage has found so far, would not be a crash. It
would be a `SignificanceResult` that looks entirely ordinary: a plausible
p-value, a plausible null distribution, `null_mean_trades` close enough to
the real count not to raise suspicion, while some smaller, undetected
mismatch quietly biases the result in a direction nobody has yet had
reason to check for. That is precisely the shape of risk this project's
own architecture document names as the hardest part of the whole system
to get right — not preventing an agent from crashing, but preventing it
from confidently reporting something plausible and wrong — and precisely
why Stage 5's own sacred gate, still ahead, treats "does the agent kill a
result that deserves to die" as the harder of its two halves.
