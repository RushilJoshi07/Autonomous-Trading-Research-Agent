"""Compiles a random-entry control strategy for the statistics tool's
significance test: same exit logic as a given StrategyRule, but entries
chosen by independent per-bar coin flips (seeded, deterministic) instead of
the rule's own entry condition.

This isolates "does entry timing matter" as the one variable under test,
holding everything else (ticker, date range, exit logic, cost model) fixed —
architecture.md §5 Step 3's mandatory control: "did it beat randomized
entries at the same trade frequency." Deliberately shares as much as
possible with rule_strategy.make_rule_strategy (indicator wiring, exit
logic) via that module's extracted helpers, so the two compiled strategies
are structurally parallel and differ only in how entries are decided.
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
