"""Tests for strategies/rule_strategy.py — the StrategyRule interpreter.

Holds the original targeted regression test for a real bug found while building
this component (kept exactly as-is, per the Stage 3 handoff document), plus the
plan §8 formal suite: each KNOWN_STRATEGY compiles and runs, positive offset
raises through the whole compiled pipeline (not just the evaluator in isolation),
and indicator dedup holds across a real KNOWN_STRATEGIES rule.

Stage 4 Component 8 adds direct coverage for unique_terms and indicator_usage,
the two small pure helpers Component 6 extracted from this file so
random_entry_strategy.py could share them. wire_indicators and apply_exit (the
other two extracted helpers) are NOT given isolated tests here — both are
already exercised, repeatedly, by every test below that compiles and runs a
real strategy through make_rule_strategy, plus every test in
test_random_entry_strategy.py that does the same through
make_random_entry_strategy. An isolated call against a stub Strategy object
would be less meaningful coverage than what already exists.
"""

import pytest

from backtester.engine import run_backtest
from backtester.evaluator import indicator_key
from backtester.registry import ALL_INDICATORS
from backtester.schema import (
    KNOWN_STRATEGIES,
    Comparison,
    Condition,
    ConstantTerm,
    IndicatorTerm,
    PriceTerm,
    StrategyRule,
)
from backtester.strategies.rule_strategy import (
    _collect_indicator_terms,
    indicator_usage,
    make_rule_strategy,
    unique_terms,
)


def _leaf(left, op, right) -> Condition:
    return Condition(kind="leaf", comparison=Comparison(left=left, op=op, right=right))


def test_deduplicated_indicator_advances_per_bar_not_static(synthetic_data):
    """Regression test for a real bug found while building this component.

    Precomputed indicators were originally stored in a plain dict
    (self._series[key] = self.I(...)). backtesting.py's run loop does NOT make
    self.I()'s return value "live" as an intrinsic property of the object —
    every bar, it re-slices whatever it discovers as a direct, top-level
    instance attribute via `isinstance(v, _Indicator)` over `strategy.__dict__`
    (backtesting/_util.py: _strategy_indicators), a scan done ONCE, right after
    init(). A dict entry pointing at the exact same object is invisible to that
    scan and is never touched again: every later read silently returned the
    full, final-length array from init() time, regardless of which bar next()
    was actually on. This produced num_trades=0 for sma_10_30_crossover, with
    NO exception raised, on data independently confirmed via plain pandas to
    contain 22 real SMA crossovers — a silent wrong answer, not a crash.

    Fixed by storing each precomputed indicator as its own named instance
    attribute (self._ind_0, self._ind_1, ...) and reading it back via getattr()
    inside the BarContext, so every read sees whatever backtesting.py most
    recently wrote to that attribute name.

    This test uses one indicator reused identically in both entry and exit —
    proving dedup still collapses it to exactly one attribute — and calls
    through self._ctx.indicator(key, offset=0), the exact method
    evaluate_condition itself calls during real rule evaluation, asserting the
    returned value changes across bars rather than being frozen. Reading the
    named attribute directly instead (self._ind_0) would NOT catch this bug:
    that attribute is always correctly re-sliced by backtesting.py's own loop
    regardless of what BarContext.indicator() does with it — the bug lives
    specifically in the lookup logic between the two, so the test has to go
    through that exact lookup, not around it. (Verified: an earlier version of
    this test read the attribute directly and still passed against the
    reintroduced dict-storage bug — a false negative caught only by
    deliberately reintroducing the bug and confirming the test failed.)
    """
    rule = StrategyRule(
        name="dedup_liveness_check",
        description="RSI(14) reused identically in entry and exit",
        entry=Condition(kind="leaf", comparison=Comparison(
            left=IndicatorTerm(name="RSI", params={"length": 14}), op="lt", right=ConstantTerm(value=50))),
        exit=Condition(kind="leaf", comparison=Comparison(
            left=IndicatorTerm(name="RSI", params={"length": 14}), op="gt", right=ConstantTerm(value=50))),
    )

    # Dedup check at the collection level, independent of the running backtest:
    # two references to the same (name, params) pair must resolve to one key.
    terms = _collect_indicator_terms(rule.entry) + _collect_indicator_terms(rule.exit)
    unique_keys = {indicator_key(t) for t in terms}
    assert len(terms) == 2
    assert len(unique_keys) == 1

    base_cls = make_rule_strategy(rule)
    observed_values: list[float] = []
    rsi_key = indicator_key(rule.entry.comparison.left)

    class InstrumentedStrategy(base_cls):
        def next(self):
            # Go through self._ctx.indicator(), the exact call evaluate_condition
            # makes — NOT getattr(self, "_ind_0") directly. See docstring.
            observed_values.append(self._ctx.indicator(rsi_key, offset=0))
            super().next()

    result = run_backtest(synthetic_data, InstrumentedStrategy, ticker="SYNTHETIC")

    assert len(observed_values) > 10, "expected next() to be called many times over 500 bars"
    distinct_values = len(set(observed_values))
    assert distinct_values > 1, (
        f"BarContext.indicator() returned the same RSI value on every one of "
        f"{len(observed_values)} calls to next() (value: {observed_values[0]}). RSI "
        f"genuinely fluctuates bar to bar on this data — a frozen value here means "
        f"the lookup is reading a stale, full-length snapshot instead of the live, "
        f"current-bar-sliced array. This is exactly the dict-storage bug this test "
        f"guards against."
    )
    assert result.num_trades > 0


@pytest.mark.parametrize("name", sorted(KNOWN_STRATEGIES))
def test_known_strategy_compiles_and_runs(name, synthetic_data):
    """Every KNOWN_STRATEGIES entry compiles via make_rule_strategy and runs via
    run_backtest with zero strategy-specific code. The three indicator-based
    strategies must produce real trades on 500 bars of synthetic data; morning
    star (a rare 3-bar reversal pattern) only needs to execute without raising --
    requiring trades from it would make the test flaky against the fixture's
    random seed, not meaningfully safer (matches the plan's own "few trades
    fine; must execute" standard for this strategy)."""
    rule = KNOWN_STRATEGIES[name]
    strategy_cls = make_rule_strategy(rule)
    result = run_backtest(synthetic_data, strategy_cls, ticker="SYNTHETIC")
    if name == "morning_star":
        assert result.num_trades >= 0
    else:
        assert result.num_trades > 0


def test_positive_offset_raises_through_full_compiled_pipeline(synthetic_data):
    """schema.py's validator blocks constructing a term with a positive offset
    directly (see test_schema.py). This proves the protection also holds through
    the WHOLE compiled pipeline -- make_rule_strategy + a real run_backtest run --
    not just at a direct, isolated evaluator.resolve_term call (test_evaluator.py
    already covers that narrower case). The term is mutated to a positive offset
    AFTER the whole rule is constructed (Pydantic models here aren't frozen or
    validate_assignment), the same bypass technique used throughout this stage's
    tests, since schema.py's construction-time validator can't see a value set
    after construction."""
    rule = StrategyRule(
        name="lookahead_leak_test",
        description="deliberately broken after construction: a positive (future) offset",
        entry=_leaf(PriceTerm(field="close", offset=-1), "gt", ConstantTerm(value=0.0)),
        exit_after_bars=5,
    )
    rule.entry.comparison.left.offset = 1  # bypasses schema.py's construction-time validator
    strategy_cls = make_rule_strategy(rule)
    with pytest.raises(ValueError, match="lookahead"):
        run_backtest(synthetic_data, strategy_cls, ticker="SYNTHETIC")


def test_sma_crossover_dedups_to_two_unique_indicators():
    """sma_10_30_crossover references SMA(10) and SMA(30) in BOTH its entry and
    exit conditions -- 4 IndicatorTerm references total, but only 2 genuinely
    distinct indicators. A lighter-weight structural check than the liveness
    test above (which owns proving dedup actually behaves correctly at runtime,
    not just that the count comes out right)."""
    rule = KNOWN_STRATEGIES["sma_10_30_crossover"]
    all_terms = _collect_indicator_terms(rule.entry) + _collect_indicator_terms(rule.exit)
    unique_keys = {indicator_key(t) for t in all_terms}
    assert len(all_terms) == 4
    assert len(unique_keys) == 2


def test_unique_terms_dedups_across_multiple_lists():
    """unique_terms takes *term_lists (variadic) specifically so
    make_random_entry_strategy can pass just exit_terms alone, with nothing
    to concatenate -- this checks the multi-list dedup itself, independent
    of which strategy-compiling function calls it."""
    sma10 = IndicatorTerm(name="SMA", params={"length": 10})
    sma10_again = IndicatorTerm(name="SMA", params={"length": 10})
    sma30 = IndicatorTerm(name="SMA", params={"length": 30})

    result = unique_terms([sma10, sma30], [sma10_again])

    assert len(result) == 2
    assert indicator_key(sma10) in result
    assert indicator_key(sma30) in result
    # first occurrence wins: the dict holds the FIRST sma10 instance seen,
    # not the second (sma10_again) -- checked by identity, not just equality,
    # since two separately-constructed IndicatorTerms with identical fields
    # would compare equal either way.
    assert result[indicator_key(sma10)] is sma10


def test_unique_terms_empty_input_gives_empty_map():
    assert unique_terms([]) == {}
    assert unique_terms() == {}


def test_indicator_usage_splits_core_and_extended():
    """SMA is core-tier; find one real VERIFIED extended-tier entry from the
    registry itself (not hand-assumed) to prove the split reads
    ALL_INDICATORS.tier correctly rather than always returning everything as
    core. Must be verified=True, not just tier="extended" -- IndicatorTerm's
    own constructor refuses any unverified indicator regardless of what this
    test is trying to check, so an unverified pick would fail before
    indicator_usage is ever reached."""
    extended_name = next(
        name for name, spec in ALL_INDICATORS.items() if spec.tier == "extended" and spec.verified
    )
    core_term = IndicatorTerm(name="SMA", params={"length": 10})
    extended_term = IndicatorTerm(name=extended_name, params={})
    terms = unique_terms([core_term, extended_term])

    core_used, extended_used = indicator_usage(terms)

    assert core_used == ["SMA"]
    assert extended_used == [extended_name]


def test_indicator_usage_empty_terms_gives_empty_lists():
    assert indicator_usage({}) == ([], [])
