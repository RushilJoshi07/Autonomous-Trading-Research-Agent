"""Tests for random_entry_strategy.py -- the significance test's
null-hypothesis generators.

Component 8 (Stage 4) formal coverage for Component 6, extended mid-Component-8
after a real, measured problem with the original probability-based approach:
confirmed directly (against real data, before any fix code was written) that
make_random_entry_strategy SATURATES well below its target trade count for
rules with a data-dependent rule.exit condition, because such a condition
only fires at a sparse, fixed set of historical bars -- no amount of entry
probability can produce more trades than there are such bars to close them
at. make_anchored_random_entry_strategy fixes this structurally for that
case; make_random_entry_strategy remains correct and is still tested here,
but only for the case it was never actually broken for: exit_after_bars-only
rules, whose exit timing is fixed and data-independent.
"""

import numpy as np
import pandas as pd
from backtesting import Backtest

from backtester.engine import run_backtest
from backtester.schema import KNOWN_STRATEGIES, Comparison, Condition, ConstantTerm, IndicatorTerm, StrategyRule
from backtester.strategies.random_entry_strategy import (
    make_anchored_random_entry_strategy,
    make_random_entry_strategy,
)
from backtester.strategies.rule_strategy import make_rule_strategy

_EXIT_AFTER_BARS_ONLY_RULE = StrategyRule(
    name="exit_after_bars_only",
    description="always-true entry, exit purely bar-count-based -- the one case make_random_entry_strategy's probability calibration was never actually broken for",
    entry=Condition(kind="leaf", comparison=Comparison(
        left=ConstantTerm(value=1), op="gte", right=ConstantTerm(value=0))),
    exit_after_bars=5,
)


# ---------------------------------------------------------------------------
# make_random_entry_strategy (probability-based) -- exit_after_bars-only rules
# ---------------------------------------------------------------------------


def test_same_seed_gives_identical_results(synthetic_data):
    cls_a = make_random_entry_strategy(_EXIT_AFTER_BARS_ONLY_RULE, n_trades=20, seed=42)
    cls_b = make_random_entry_strategy(_EXIT_AFTER_BARS_ONLY_RULE, n_trades=20, seed=42)
    result_a = run_backtest(synthetic_data, cls_a, ticker="SYNTHETIC")
    result_b = run_backtest(synthetic_data, cls_b, ticker="SYNTHETIC")
    assert result_a.num_trades == result_b.num_trades
    assert result_a.trade_returns == result_b.trade_returns


def test_different_seeds_give_different_results(synthetic_data):
    """Proves the randomness is actually happening, not silently frozen -- a
    test that only checked "it runs without raising" could pass even if
    self._rng.random() somehow always returned the same value."""
    results = []
    for seed in range(5):
        cls = make_random_entry_strategy(_EXIT_AFTER_BARS_ONLY_RULE, n_trades=20, seed=seed)
        results.append(run_backtest(synthetic_data, cls, ticker="SYNTHETIC"))
    trade_counts = {r.num_trades for r in results}
    assert len(trade_counts) > 1, "5 different seeds produced the identical trade count every time"


def test_expected_trade_count_is_reasonably_calibrated_for_exit_after_bars_rules(synthetic_data):
    """entry_prob = n_trades / len(data) targets n_trades in EXPECTATION, not
    on every individual draw. For an exit_after_bars-only rule specifically
    -- the one case this function is still used for after the anchored
    redesign -- checked directly (before writing this assertion) that a
    target of 10 realizes a mean of ~8.8 over 20 seeds (ratio 0.88): well
    calibrated, not the severe saturation a data-dependent rule.exit showed
    (ratio 0.14 at a comparable target). The tolerance below is generous,
    not tight, because even this well-behaved case has real, expected
    variation -- not because the calibration is known to be poor here."""
    target = 10
    counts = []
    for seed in range(20):
        cls = make_random_entry_strategy(_EXIT_AFTER_BARS_ONLY_RULE, n_trades=target, seed=seed)
        counts.append(run_backtest(synthetic_data, cls, ticker="SYNTHETIC").num_trades)
    mean_count = np.mean(counts)
    assert abs(mean_count - target) < target * 0.35, (
        f"mean trade count {mean_count} across 20 seeds is too far from target {target} for an "
        "exit_after_bars-only rule -- this case was confirmed well-calibrated; a failure here "
        "would mean that's no longer true"
    )


def test_only_exit_indicators_are_wired_not_entry_indicators():
    """A rule whose ENTRY references an indicator and whose EXIT is purely
    exit_after_bars must compile with ZERO indicators wired -- proving
    entry-side indicators are never even computed for a random control,
    matching the design decision that rule.entry is irrelevant once entries
    are coin flips, not rule-evaluated conditions."""
    rule = StrategyRule(
        name="entry_only_indicator",
        description="entry uses RSI, exit is purely bar-count-based",
        entry=Condition(kind="leaf", comparison=Comparison(
            left=IndicatorTerm(name="RSI", params={"length": 14}), op="lt", right=ConstantTerm(value=30))),
        exit_after_bars=5,
    )
    strategy_cls = make_random_entry_strategy(rule, n_trades=20, seed=0)
    assert strategy_cls.indicators_used == []
    assert strategy_cls.extended_indicators_used == []


def test_exit_after_bars_is_respected(synthetic_data):
    """Every trade a random-entry control takes must be held for AT MOST
    exit_after_bars + 1 bars -- proving apply_exit's shared logic (the same
    function make_rule_strategy calls) actually fires for this strategy
    type too, checked directly against backtesting.py's own per-trade
    EntryBar/ExitBar record rather than an aggregate stat that could hide
    one over-long trade among many short ones.

    The "+1" is deliberate, not a loosened bound to make this pass: traced
    directly (bt.run() on a controlled example, EntryBar/ExitBar inspected
    per trade) before writing this assertion. bars_held (compared against
    exit_after_bars inside apply_exit) is measured relative to entry_bar,
    which backtesting.py already records as the FILL bar -- one bar after
    the buy() signal, its standard next-bar-execution model. The close
    order apply_exit places, once bars_held >= exit_after_bars, is itself
    subject to that same one-bar-later fill. Two one-bar delays compound:
    a real trace showed 42 of 43 trades held for EXACTLY 4 bars with
    exit_after_bars=3, not 3 -- confirming this is backtesting.py's
    execution-timing model working as designed, not a defect in apply_exit,
    and confirming exit_after_bars=3 genuinely bounds how long a position
    is held, just not at the exact number naively expected."""
    rule = StrategyRule(
        name="short_hold",
        description="random entries, forced exit after 3 bars",
        entry=Condition(kind="leaf", comparison=Comparison(
            left=ConstantTerm(value=1), op="gte", right=ConstantTerm(value=0))),
        exit_after_bars=3,
    )
    strategy_cls = make_random_entry_strategy(rule, n_trades=60, seed=1)
    bt = Backtest(synthetic_data, strategy_cls, commission=0.001, cash=10_000, exclusive_orders=True, finalize_trades=True)
    stats = bt.run()
    trades = stats["_trades"]
    assert len(trades) > 0
    holding_periods = trades["ExitBar"] - trades["EntryBar"]
    assert (holding_periods <= 4).all()


# ---------------------------------------------------------------------------
# make_anchored_random_entry_strategy -- rules with a data-dependent rule.exit
# ---------------------------------------------------------------------------


def _real_exit_bars(rule, price_data) -> list[int]:
    """The real strategy's own historical exit bars, via the same
    engine.run_backtest -> BacktestResult.exit_bars path test_significance
    itself uses to build an anchored control."""
    observed = run_backtest(price_data, make_rule_strategy(rule), ticker="SYNTHETIC")
    return observed.exit_bars


def test_anchored_control_produces_exactly_n_trades(synthetic_data):
    """The core guarantee: one control trade per real historical exit bar,
    by construction -- not "approximately," the way the probability-based
    approach could only ever promise for this rule shape. sma_10_30_crossover
    on the standard fixture has well-spaced real exits (15-80 bars apart,
    confirmed directly during this investigation), so no anchor should need
    to be skipped for a tight gap here."""
    rule = KNOWN_STRATEGIES["sma_10_30_crossover"]
    exit_bars = _real_exit_bars(rule, synthetic_data)
    assert len(exit_bars) > 0

    strategy_cls = make_anchored_random_entry_strategy(rule, exit_bars, seed=0)
    result = run_backtest(synthetic_data, strategy_cls, ticker="SYNTHETIC")

    assert result.num_trades == len(exit_bars)


def test_anchored_control_exits_match_the_real_historical_bars_exactly(synthetic_data):
    """"Keeping the same real exit point" checked literally: the control's
    OWN recorded ExitBar values (not just its trade count) must equal the
    real strategy's exit_bars exactly, bar for bar."""
    rule = KNOWN_STRATEGIES["sma_10_30_crossover"]
    exit_bars = _real_exit_bars(rule, synthetic_data)

    strategy_cls = make_anchored_random_entry_strategy(rule, exit_bars, seed=2)
    bt = Backtest(synthetic_data, strategy_cls, commission=0.001, cash=10_000, exclusive_orders=True, finalize_trades=True)
    stats = bt.run()
    control_exit_bars = sorted(stats["_trades"]["ExitBar"].tolist())

    assert control_exit_bars == sorted(exit_bars)


def test_anchored_control_same_seed_gives_identical_entries(synthetic_data):
    rule = KNOWN_STRATEGIES["sma_10_30_crossover"]
    exit_bars = _real_exit_bars(rule, synthetic_data)
    a = run_backtest(synthetic_data, make_anchored_random_entry_strategy(rule, exit_bars, seed=7), ticker="SYNTHETIC")
    b = run_backtest(synthetic_data, make_anchored_random_entry_strategy(rule, exit_bars, seed=7), ticker="SYNTHETIC")
    assert a.trade_returns == b.trade_returns


def test_anchored_control_different_seeds_randomize_entries(synthetic_data):
    """Exit points are fixed by design -- it's specifically the ENTRY points
    that must vary across seeds, proving the randomization is actually
    happening on the intended half of each trade."""
    rule = KNOWN_STRATEGIES["sma_10_30_crossover"]
    exit_bars = _real_exit_bars(rule, synthetic_data)
    returns_by_seed = set()
    for seed in range(5):
        result = run_backtest(synthetic_data, make_anchored_random_entry_strategy(rule, exit_bars, seed=seed), ticker="SYNTHETIC")
        returns_by_seed.add(tuple(result.trade_returns))
    assert len(returns_by_seed) > 1, "5 different seeds produced identical trade returns every time"


def test_anchored_control_skips_anchor_when_historical_gap_is_too_tight():
    """The documented, disclosed exception: when two consecutive real exits
    are too close together to fit a valid, non-overlapping entry window,
    that anchor is skipped rather than forced -- checked directly with a
    deliberately pathological exit_bars list (consecutive bars 1 apart),
    not left as an untested claim in a docstring. Confirms the skip fails
    safely (fewer trades, not a crash or an overlapping position)."""
    rng = np.random.default_rng(3)
    n = 200
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    df = pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1_000_000.0},
        index=pd.bdate_range("2020-01-01", periods=n, name="Date"),
    )
    # exit_bars is passed explicitly and is all this function actually uses for
    # its own logic -- rule only needs to satisfy StrategyRule's own "exit
    # and/or exit_after_bars" requirement, its specific value is irrelevant here.
    rule = StrategyRule(
        name="tight_gap_test",
        description="exit_bars deliberately packed too tight for all of them to be usable",
        entry=Condition(kind="leaf", comparison=Comparison(
            left=ConstantTerm(value=1), op="gte", right=ConstantTerm(value=0))),
        exit_after_bars=5,
    )
    # 50, 51, 52: gaps of 1 bar -- too tight (high = exit-2 < low = prev_exit+1).
    # 100: a normal, usable gap after the cluster.
    pathological_exit_bars = [50, 51, 52, 100]

    strategy_cls = make_anchored_random_entry_strategy(rule, pathological_exit_bars, seed=0)
    bt = Backtest(df, strategy_cls, commission=0.001, cash=10_000, exclusive_orders=True, finalize_trades=True)
    stats = bt.run()
    trades = stats["_trades"]

    assert len(trades) < len(pathological_exit_bars), (
        "expected at least one tight-gap anchor to be skipped, but got as many trades as anchors"
    )
    assert len(trades) >= 1, "the one well-spaced anchor (bar 100) should still have produced a trade"
