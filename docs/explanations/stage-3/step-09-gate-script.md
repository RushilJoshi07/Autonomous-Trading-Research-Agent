# Step 9 — The Stage 3 Gate Script (Stage 3, plan §9)

## 1. What this does

`scripts/verify_stage3_gate.py` is the actual Stage 3 verification gate —
`docs/architecture.md`'s build-order table lists Stage 3's gate as
"Literature-consistent results," and this script is the thing that checks
that, for real, against real market data. Every prior Stage 3 component was
verified in isolation: Component 8's extended-indicator scripts against
synthetic bars, plan §8's 170-test suite against one fixed synthetic fixture.
None of that proves the *whole compiled pipeline* — schema, evaluator,
indicator registry, and the interpreter that wires them into a real
`backtesting.py` `Strategy` — produces sane, literature-consistent numbers
when it's finally pointed at genuine history instead of a synthetic random
walk. This script closes that gap: it runs all four `KNOWN_STRATEGIES`
against real AAPL daily bars, 2015-01-01 through 2024-12-31, and checks trade
counts, Sharpe ratios, and max drawdowns against bounds drawn from the
literature each strategy comes from (Brock/Lakonishok/LeBaron for SMA
crossover, Wilder for RSI(14), Connors & Alvarez for RSI(2), Nison for
morning star — the same sources cited in `schema.py`'s `KNOWN_STRATEGIES`).

On the first real run, this gate **passed** — three strategies cleanly, one
(`rsi_14_30_70`) via a formally disclosed and independently investigated
exception, described in full below. That result, and what it does and does
not prove about Stage 3 as a whole, is the substance of this document.

What this is *not*: it is not a claim that these four strategies are good
trading ideas, or that AAPL is a representative test case, or that Stage 3 as
a whole is formally closed — plan §10 (the Alembic baseline) is still an open
checklist item, deferred to "stage close," which is now due but not yet done.
Whether a passing gate on its own means the stage is closed is a question
this document raises but does not answer; the answer is a human decision
made outside this file, not something to assert here.

---

## 2. Every meaningful line explained

### Confirming the data existed before writing a line of ingestion code

Before any code was written, the local Postgres cache
(`strategy_research`/`price_bars`) was queried directly:

```sql
SELECT ticker, COUNT(*), MIN(date), MAX(date) FROM price_bars WHERE ticker='AAPL' GROUP BY ticker;
-- AAPL | 4164 | 2010-01-04 | 2026-07-24
```

Real AAPL data already existed, comfortably covering the requested window —
Stage 1's ingestion pipeline had already done this work, months earlier, as
its own deliverable. This script therefore contains zero ingestion logic; it
calls the existing `load_price_data(ticker, session, start, end)` from
`src/backtester/data_loader.py`, the exact function `test_engine.py` already
calls against the synthetic fixture, just pointed at real dates instead. Not
checking this first would have risked either writing redundant ingestion code
that duplicated Stage 1's own job, or — worse — silently falling back to
synthetic data for a script whose entire purpose is proving the pipeline
works on *real* data.

### The bounds table's two different kinds of check

```python
BOUNDS: dict[str, dict] = {
    "sma_10_30_crossover": {
        "min_trades": 10, "max_trades": 80, "max_sharpe": 3.0, "max_dd_pct": -1.0,
        ...
    },
    ...
}
```

Two of these four numbers check in opposite directions, and getting that
backwards would silently invert the whole gate. `max_sharpe` is a **ceiling**:
`sharpe_ratio >= 3.0` fails. This is deliberately the same signature Stage
2's own `LookaheadStrategy` gate test (`tests/backtester/test_sacred_gate.py`)
uses to *detect* lookahead bias — an implausibly high, too-good-to-be-real
Sharpe ratio over a genuine multi-year backtest is a red flag for the
strategy secretly seeing the future, not a sign of a good strategy. There is
deliberately no *floor* on Sharpe: a legitimately mediocre or negative Sharpe
is not this gate's concern, because Stage 3 is verifying that the *pipeline*
computes correctly, not that these four literature strategies happen to be
profitable on this one ticker.

`max_dd_pct` is the opposite shape: a **floor**, not a ceiling.
`max_drawdown_pct` is always ≤ 0 (`test_engine.py`'s
`test_max_drawdown_is_non_positive` already established this), so
`max_dd_pct = -1.0` with the check `max_drawdown_pct < -1.0` means the
strategy must have drawn down by *at least* 1% at some point across ~10 years
of real daily bars. A near-zero max drawdown on genuine multi-year market
history is itself the suspicious signal here — it usually means barely any
real position risk was ever taken, which for a strategy meant to hold
positions through market moves would point at something wrong upstream
(orders not actually filling, a condition that's nearly always false, or
similar), not a genuinely low-risk edge.

`morning_star`'s row has `max_trades: None` and `max_dd_pct: None` — no
upper bound on trades, no drawdown floor at all. A 3-bar reversal candlestick
pattern is legitimately rare; forcing it into the same shape of bounds as the
three indicator-driven strategies would either make the gate flaky (failing
on a coin-flip of how many patterns happen to appear in one specific decade)
or require picking an arbitrary, unjustified range with no literature backing
it the way the other three strategies' ranges do.

### `check_one` returns structured problems, not strings

```python
def check_one(name, rule, bounds, data) -> tuple[list[tuple[str, str]], BacktestResult]:
    ...
    problems: list[tuple[str, str]] = []
    if result.num_trades < bounds["min_trades"]:
        problems.append(("min_trades", f"num_trades={result.num_trades} < min {bounds['min_trades']} ..."))
    ...
```

Each problem is a `(bound_key, message)` pair, not just a message string.
This shape only exists because of what section 3 below describes: `main()`
needs to reconcile a specific violated bound against a specific,
pre-approved exception, and a plain string can't be matched reliably against
a dictionary key — `"num_trades=12 < min 20 (check: thresholds)"` would need
fragile substring matching to identify *which bound* it's about, and would
silently break the moment the message wording changed for any reason
unrelated to the bound itself. `bound_key` values (`"min_trades"`,
`"max_trades"`, `"max_sharpe"`, `"max_dd_pct"`) are stable identifiers,
independent of the human-readable text.

`check_one` never raises for an out-of-bounds *value* — only a genuine
pipeline failure (a compile error inside `make_rule_strategy`, an exception
during `run_backtest` itself) is allowed to propagate up and halt the script.
This is the same "expected failure vs. real bug" distinction
`generate_extended_indicators.py`'s classification phase already draws for
extended indicators: a number outside the literature range is data the gate
needs to *report on*, not an exceptional condition that should crash the
whole run before the other three strategies get evaluated.

---

## 3. Design decisions and rejected alternatives — the `rsi_14_30_70` investigation

This is the real substance of this component. On the first real run, three
strategies passed; `rsi_14_30_70` did not: `num_trades=12`, below the bound's
minimum of 20. Sharpe was 0.577 (nowhere near the 3.0 lookahead ceiling), so
the plan's own diagnostic table's "≥3.0 → lookahead" hint didn't apply — the
only live lead was the "thresholds" hint attached to the *zero*-trades case,
which technically didn't fit either, since 12 is not 0.

**The investigation, in the order it actually happened.** Rather than guess
at a fix, or worse, propose loosening the bound, the first step was
reproducing the underlying signal *completely independent of this
codebase's own evaluator and rule-compilation logic* — the same isolation
principle Component 5's dict-storage bug and Component 7's Bedrock
credential failures were both root-caused with. `pandas_ta.rsi(close,
length=14)` was computed directly on the real AAPL series, with no
`evaluator.py`, no `rule_strategy.py`, no `backtesting.py` involved at all.
Counting raw threshold crossings this way found 71 separate crosses below 30
and 335 separate crosses above 70 over the decade — dramatically more than
12. On its own, this number proved nothing yet: raw crossing counts are not
the same thing as trade counts in a long-only, single-position system, where
an entry can only happen while flat and an exit can only happen while
holding a position.

So a second, more precise independent check was built: a small, hand-written
plain-Python state machine, walking the same real RSI series bar by bar,
applying the *exact* entry/exit rule `rule_strategy.py`'s compiled strategy
uses (enter only if not already in a position and RSI crosses below 30; exit
only if in a position and RSI crosses above 70) — still with zero dependency
on this codebase's own pipeline code. That simulation reproduced
`num_trades=12` **exactly**. This is the load-bearing evidence: it means
`rule_strategy.py`'s compiled strategy is doing precisely what a
from-scratch, independently-written implementation of the same rule would
do. The gap between "71 raw crossings" and "12 real trades" is not a bug —
it's what a single-position long-only strategy is *supposed* to do: most of
those 71 oversold dips happen in clusters while a position from an earlier
dip is already open, still waiting for the eventual cross above 70 to exit,
so they don't generate new entries. Only 12 genuine flat-to-long transitions
occur across the whole decade. AAPL's real behavior over exactly this window
corroborates why: the other two indicator strategies both show strong
positive returns over the identical dates (`sma_10_30_crossover` +304.67%,
`rsi_2_10_90` +62.53%) — a persistently trending stock naturally produces
fewer "oversold, then recovers" cycles than the 20–200 range (drawn from
broader multi-asset, multi-decade academic literature, not one ticker's one
decade) anticipates.

**What was explicitly rejected, and why.** Two alternatives were on the
table once this was understood to be a real, non-bug deviation, and both
were explicitly declined before any code was touched. The first: quietly
widen `BOUNDS["rsi_14_30_70"]["min_trades"]` from 20 to something below 12.
Rejected for the same reason `docs/architecture.md`'s own screener section
warns against hand-picked, retunable thresholds — "a hand-picked number can
be quietly retuned until the backtest looks good... that is overfitting
hidden in the universe definition." The exact same failure mode applies to a
verification gate: a bound that gets adjusted the moment a result doesn't
meet it stops being a real check and becomes theater. The second: accept the
result verbally, in conversation, and move on without changing anything in
the repository. Rejected because this project's own established pattern for
disclosed limitations — the survivorship-bias coverage-gap disclosure in the
data layer — is that a real limitation gets recorded *in the system's actual
output*, not just discussed once and left to institutional memory. A future
run of this exact script, months from now, would show `FAIL` with zero trace
of any of this investigation, and whoever's running it would either have to
redo the whole investigation from scratch or — more likely — "fix" it by
doing exactly the first rejected alternative.

**What was chosen instead: `KNOWN_DEVIATIONS`.**

```python
KNOWN_DEVIATIONS: dict[tuple[str, str], str] = {
    ("rsi_14_30_70", "min_trades"): (
        "2026-08-13: num_trades=12 on real AAPL 2015-01-01..2024-12-31, below "
        "the literature-consistent floor of 20. Independently verified correct, "
        "not a pipeline bug, via a standalone plain-Python simulation ..."
    ),
}
```

Keyed `(strategy_name, bound_key)`, not just `strategy_name` — this was a
deliberate scoping choice. A coarser key (just the strategy name) would mean
accepting *any* future violation on `rsi_14_30_70`, including one on a
completely different bound (`max_sharpe`, say) that was never actually
investigated — silently laundering an unrelated, unexamined failure through
an exception that was only ever earned for `min_trades`. The fine-grained key
means an accepted deviation covers exactly the one claim that was actually
checked.

Every reason string is required — by explicit instruction, not by default —
to be dated and to name the independent verification method in enough
detail that someone could redo it themselves without re-deriving the
approach from scratch. A vaguer note like "investigated, seems fine" would
satisfy "disclosed" but not "durable": the whole point of writing this down
is that the verification is *reproducible*, not just asserted to have
happened once.

**`MAX_DEVIATIONS_BEFORE_REVIEW = 3`.** A second, separate design question:
what stops `KNOWN_DEVIATIONS` from quietly becoming a dumping ground where
every future failure gets rationalized and added, one at a time, until the
gate always reports green regardless of what's actually wrong? The chosen
answer treats *accumulation itself* as a signal, independent of how well any
individual entry is investigated: once the total count of matched, accepted
deviations across all four strategies reaches 3, the gate refuses to report
a clean pass — exit code 1 — with an explicit message that the bounds
table's assumptions (or the choice of ticker and window) need reconsidering.
Three was chosen deliberately low: with 4 strategies and up to 3 checkable
bounds each (12 possible violation slots total), and given the explicit
framing that one deviation is legitimate while five or six is concerning,
three sits meaningfully before the "concerning" end of that range — an early
trigger, not a warning that only fires once the situation is already bad.
The alternative — a higher threshold, or none at all — was rejected because
it would let this exact mechanism, built specifically to keep exceptions
honest and visible, become the thing that makes them easy to ignore instead.

---

## 4. Concepts introduced

**Literature-consistent bounds as a verification technique.** Rather than
asserting a strategy behaves "correctly" against some absolute standard (there
isn't one — nobody can prove what AAPL's RSI(14) crossovers "should" produce),
this gate checks that the pipeline's output falls within a range independently
established by prior published research on the same strategy family, applied
to a similar (though never identical) market and time period. This is a
weaker but more honest claim than "this strategy works" — it's closer to "this
pipeline's numbers aren't obviously broken," which is exactly what Stage 3 is
actually trying to prove.

**The difference between a raw signal count and a stateful trade count.**
The `rsi_14_30_70` investigation is the concrete example: 71 raw RSI
threshold crossings produced only 12 actual trades, because a long-only,
single-position strategy's entries are gated on *not already holding a
position*. This is a general lesson beyond this one strategy: any time a
signal is evaluated bar-by-bar against a stateful position-management rule,
the number of "signal fires" and the number of "position changes" can differ
by a large factor, and conflating them (assuming a strategy should trade
roughly as often as its raw signal fires) is a natural but wrong intuition
worth naming explicitly.

**Disclosed exceptions vs. silent threshold-tuning.** Already covered above
for this specific case; the general principle — a verification bound that can
be quietly adjusted the moment a result fails it is not a real check — applies
anywhere a project has automated gates with human-chosen thresholds, not just
here.

---

## 5. How the gate was satisfied — and what it does and does not prove

The gate, run for real against the live database (not a mock, not a
recorded fixture): 3 of 4 strategies passed cleanly (`morning_star`: 8
trades, Sharpe −0.404; `rsi_2_10_90`: 73 trades, Sharpe 0.221;
`sma_10_30_crossover`: 44 trades, Sharpe 0.678, +304.67% total return);
`rsi_14_30_70` passed via one formally disclosed, independently verified
deviation (`num_trades=12` against a floor of 20). Final exit code: `0`,
with the report explicitly labeled "PASSED, with 1 disclosed and
investigated deviation(s)" — a distinct state from an unqualified clean
pass, printed and visible on every future run, not silently collapsed into
plain success.

The `KNOWN_DEVIATIONS` and `MAX_DEVIATIONS_BEFORE_REVIEW` mechanisms were
themselves verified, not just assumed to work, following this project's
established "test both ways" discipline (the Component 5 dedup test, the
plan §8 `validate_offset` bug-reintroduction check). The real run's exit
code 0 confirms the happy path. A separate, isolated check confirmed the
threshold actually blocks: `BOUNDS` was temporarily monkeypatched to force
two *additional genuine* bound violations (not just two more
`KNOWN_DEVIATIONS` entries — the first attempt at this check tried exactly
that, and found it did nothing at all, because a deviation entry only
suppresses a problem that actually occurred; it can't manufacture one where
none exists, since `sma_10_30_crossover` and `rsi_2_10_90` genuinely satisfy
their real bounds), then confirmed the script correctly printed `BLOCKED: 3
disclosed deviations >= review threshold (3)...` and exited 1. The real,
unmodified script and its real result were unaffected by this check, run in
a separate, isolated Python process. The full 170-test suite was re-run
afterward and confirmed unaffected — this script isn't imported by any
existing test and touches no `src/` files.

**What this does not prove.** One ticker, one decade. Nothing about this
gate says the pipeline would produce sane numbers on a different asset class,
a different market regime, or point-in-time survivorship-corrected universe
data (a documented, disclosed limitation of Stage 1's data layer, unrelated
to and unaffected by this gate). The gate also does not, and is not designed
to, say anything about whether these four strategies are *good* — a
consistently negative-Sharpe morning-star result and a wildly profitable SMA
crossover both "pass" the same gate, because the gate checks pipeline
correctness signatures (plausible trade counts, no implausible Sharpe,
realistic drawdown), not strategy quality. And the one disclosed deviation,
while independently verified as not-a-bug, means this specific gate run is
not a *clean* pass in the strictest sense — a reader should come away
knowing the difference between "0 issues" and "1 issue investigated and
accepted," and this document is written so that distinction survives.

---

## 6. Interview defense

**Q: Why didn't you just loosen the `rsi_14_30_70` bound from 20 to, say,
10, once you confirmed 12 trades was correct?**

A: Because a bound that gets adjusted the moment a real result doesn't meet
it stops functioning as a check at all — it becomes a description of
whatever the code happens to produce, which is exactly the "overfitting
hidden in the threshold" failure mode this project's own architecture
document warns against for the screener's universe filters. The literature
range (20–200) reflects broader multi-asset, multi-decade research; AAPL's
one specific decade legitimately falling outside it is a real, disclosable
fact about *this test case*, not evidence the range itself is wrong. Keeping
the original bound and disclosing the specific, investigated exception
preserves the bound's meaning for every other run and every other ticker
this gate might someday check.

**Q (hard): Isn't a mechanism that lets a failing check still report "PASS"
just a more sophisticated way of hiding failures?**

A: The honest answer is that it's a real risk, which is exactly why
`MAX_DEVIATIONS_BEFORE_REVIEW` exists — the mechanism is deliberately
designed to stop being able to hide anything once exceptions start
accumulating, regardless of how well any individual one is argued. The
difference between this and "hiding failures" is threefold: every deviation
requires independent, reproducible verification before it can be added (not
just an assertion that it's fine); every deviation is printed in full on
every single run, forever, not filed away and forgotten; and the mechanism
actively refuses to keep granting exceptions past a low, deliberately-early
threshold. A system that hides failures makes them invisible. This one makes
them impossible to miss, while still distinguishing "investigated and
understood" from "genuinely broken" — which a bare pass/fail signal cannot
do at all.

**Q: Why does `max_dd_pct` check as a floor instead of a ceiling? Doesn't a
verification gate normally want to catch *excessive* risk, not insufficient
risk?**

A: For a risk-management system, yes — but that's not what this specific
check is verifying. This gate isn't judging whether a strategy's risk level
is acceptable; it's using drawdown as a sanity check that the strategy
actually did something over ten years of real, volatile market history. A
strategy that shows a near-zero max drawdown across a decade that included
real market stress is more likely to indicate a wiring problem (positions
not actually being held, or a condition that's nearly always false) than a
genuinely low-risk edge — the same logic Stage 2's own lookahead gate uses
for implausibly high Sharpe, just applied to the opposite tail of "too good
to be a real backtest."

**Honest weakness:** this gate ran against exactly one ticker (AAPL) and one
specific decade, both chosen by the plan rather than tested for robustness
across multiple names or windows. A pipeline bug specific to a stock with
different volatility characteristics, or a regime this particular decade
didn't include (a sustained bear market, for instance — AAPL's 2015–2024
window was overwhelmingly a bull run), would not be caught by this gate at
all. That's a real, disclosed limitation of Stage 3's scope, not something
to claim otherwise under questioning.

---

## 7. What comes next and why

This document deliberately stops short of declaring Stage 3 fully closed.
`docs/architecture.md`'s stated gate criterion for Stage 3 — literature-
consistent results — has now passed for real, which is the substantive
technical claim this step exists to prove and does prove. But
`docs/plans/stage-3-plan.md`'s own verification checklist (section
"Verification," item 6) separately lists the Alembic migration baseline as
required before Stage 3's own build-order description calls the work done,
and that item — explicitly deferred to "stage close" throughout this stage's
prior components — is now due but not yet built. Whether a passing gate on
its own is sufficient to trigger `CLAUDE.md`'s Level-3 stage-synthesis
explanation, or whether that should wait for Alembic too, is a judgment call
being surfaced to the user directly rather than decided here.

If this gate's result were wrong in a way nobody caught — if, say, the
`rsi_14_30_70` investigation's independent simulation had a subtle bug
matching the pipeline's own subtle bug by coincidence — the most likely place
it would eventually surface is Stage 4, when these same `KNOWN_STRATEGIES`
get wrapped as MCP tools and exercised by real tool calls for the first time,
or Stage 5, when the research agent starts generating and testing genuinely
novel hypotheses built from the same registry and evaluator this gate just
verified. A pipeline bug hiding behind a matching independent-simulation bug
is a low-probability but nonzero risk worth naming, not a scenario this
document can fully rule out.
