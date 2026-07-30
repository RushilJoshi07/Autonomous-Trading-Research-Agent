"""Tests for strategies/rule_strategy.py — the StrategyRule interpreter.

Currently holds one targeted regression test for a real bug found while building
this component; the full Component 8 test suite (each KNOWN_STRATEGY, positive
offset raises through the whole pipeline, etc.) is written when Component 8 begins.
"""

from backtester.engine import run_backtest
from backtester.evaluator import indicator_key
from backtester.schema import Comparison, Condition, ConstantTerm, IndicatorTerm, StrategyRule
from backtester.strategies.rule_strategy import _collect_indicator_terms, make_rule_strategy


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
