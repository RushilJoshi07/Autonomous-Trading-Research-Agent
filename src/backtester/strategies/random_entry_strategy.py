"""Compiles random-entry control strategies for the statistics tool's
significance test — two variants, for two structurally different exit shapes.

Both isolate "does entry timing matter" as the one variable under test,
holding everything else (ticker, date range, exit logic, cost model) fixed —
architecture.md §5 Step 3's mandatory control: "did it beat randomized
entries at the same trade frequency."

make_random_entry_strategy (probability-based, entries as independent coin
flips) is correct ONLY for rule.exit_after_bars-only rules, whose exit
timing is fixed and data-independent — any entry gets a guaranteed,
same-length hold, so trade count scales cleanly with entry probability.

For rules with a data-dependent rule.exit condition, a real, measured
problem with the probability approach motivated make_anchored_random_entry_strategy
instead: confirmed directly (Stage 4 Component 8) that rule.exit conditions
fire at a SPARSE, fixed set of historical bars (10 events in 500 bars for
sma_10_30_crossover) — every realized trade must close at one of those
events, so realized trade count SATURATES well below the intended target
once entry probability is high enough to claim most of them, and pushing
probability higher doesn't help (measured: target 80 -> mean realized 10.9,
a 0.14 ratio, not a rare edge case). The anchored variant fixes this
structurally rather than tuning the probability: it reuses the real
strategy's own historical exit bars directly, pairing one randomized entry
with each one, guaranteeing the trade count by construction instead of by
chance.

Both variants share as much as possible with rule_strategy.make_rule_strategy
via that module's extracted helpers, so every compiled strategy in this
project stays structurally comparable.
"""

from __future__ import annotations

import numpy as np
from backtesting import Strategy

from ..evaluator import BarContext
from ..schema import StrategyRule
from .rule_strategy import (
    _collect_indicator_terms,
    _RuleBarContext,
    apply_exit,
    indicator_usage,
    unique_terms,
    wire_indicators,
)


def make_random_entry_strategy(rule: StrategyRule, n_trades: int, seed: int) -> type[Strategy]:
    """Compile a random-entry control matched to rule's exit logic and approximate trade frequency.

    Only correct for rule.exit_after_bars-only rules (rule.exit is None) —
    see the module docstring for why a data-dependent rule.exit needs
    make_anchored_random_entry_strategy instead.

    Entry probability per eligible bar is n_trades / (bar count), so the EXPECTED
    trade count across many draws approximates n_trades — not an exact per-draw
    guarantee, which is the correct behavior for a Monte Carlo null distribution
    (real random variation in trade count is itself part of what "random" should
    look like, not something to suppress).

    Only rule.exit's indicator terms are wired up — rule.entry's terms are
    irrelevant here, since entry is decided by a coin flip, not evaluated.
    """
    exit_terms = _collect_indicator_terms(rule.exit) if rule.exit is not None else []
    terms = unique_terms(exit_terms)
    indicators_used, extended_indicators_used = indicator_usage(terms)

    class RandomEntryStrategy(Strategy):
        def init(self) -> None:
            self._key_to_attr = wire_indicators(self, terms)
            self._ctx: BarContext = _RuleBarContext(self)
            self._rng = np.random.default_rng(seed)
            self._entry_prob = n_trades / len(self.data)

        def next(self) -> None:
            if not self.position:
                if self._rng.random() < self._entry_prob:
                    self.buy()
                return
            apply_exit(self, rule, self._ctx)

    RandomEntryStrategy.__name__ = f"RandomEntryStrategy_{rule.name}_seed{seed}"
    RandomEntryStrategy.__qualname__ = RandomEntryStrategy.__name__
    RandomEntryStrategy.indicators_used = indicators_used
    RandomEntryStrategy.extended_indicators_used = extended_indicators_used
    return RandomEntryStrategy


def make_anchored_random_entry_strategy(rule: StrategyRule, exit_bars: list[int], seed: int) -> type[Strategy]:
    """Compile a random-entry control anchored to the real strategy's own
    historical exit bars — one control trade per real exit, entry point
    randomized, exit point fixed to match the real trade's own exit bar.

    Guarantees len(exit_bars) trades by construction, EXCEPT when a
    historical gap between two consecutive real exits is too short to fit a
    valid, non-overlapping entry window — that anchor is skipped rather than
    forced into an invalid entry, so the true guarantee is "len(exit_bars)
    trades, minus however many anchors had a too-short gap to use," a
    disclosed exception rather than an unconditional promise. Not expected
    to trigger on typical daily-bar data (the shortest real gap measured in
    Component 8's own verification was 15 bars).

    Bar-bookkeeping detail, load-bearing for the exact windowing below: both
    entry and exit ORDERS in backtesting.py fill one bar after the next()
    call that signals them. exit_bars entries are FILL bars (as recorded in
    backtesting.py's own _trades table) — so to make a control's exit fill
    at the same bar exit_bars[i] recorded historically, its close() SIGNAL
    must fire one bar earlier, at exit_bars[i] - 1.

    Does not wire up or evaluate rule.exit's condition at all — it doesn't
    need to; the historical exit bars already ARE that condition's real,
    already-computed effect. indicator_usage is still called for accurate
    provenance reporting (what rule.exit referenced), independent of whether
    this strategy actually computes it.
    """
    exit_terms = _collect_indicator_terms(rule.exit) if rule.exit is not None else []
    terms = unique_terms(exit_terms)
    indicators_used, extended_indicators_used = indicator_usage(terms)

    sorted_exits = sorted(exit_bars)

    class AnchoredRandomEntryStrategy(Strategy):
        def init(self) -> None:
            rng = np.random.default_rng(seed)
            self._entry_bars: set[int] = set()
            self._exit_signal_bars: set[int] = set()
            prev_exit = -1
            for exit_fill_bar in sorted_exits:
                low = prev_exit + 1
                high = exit_fill_bar - 2  # last signal bar that can still fill and hold >=1 bar
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

    AnchoredRandomEntryStrategy.__name__ = f"AnchoredRandomEntryStrategy_{rule.name}_seed{seed}"
    AnchoredRandomEntryStrategy.__qualname__ = AnchoredRandomEntryStrategy.__name__
    AnchoredRandomEntryStrategy.indicators_used = indicators_used
    AnchoredRandomEntryStrategy.extended_indicators_used = extended_indicators_used
    return AnchoredRandomEntryStrategy
