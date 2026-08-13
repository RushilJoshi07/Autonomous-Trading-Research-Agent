"""Tests for schema.py's StrategyRule schema and validators.

Formal automated coverage for Components 3+8 (see test_indicator_core.py's module
docstring for why this ships with the extended-indicator generation component
rather than being deferred). schema.py's IndicatorTerm validator is one of the two
places this component actually edits (CORE_INDICATORS -> ALL_INDICATORS), so it
gets explicit regression protection here, including a case that specifically
exercises an extended-tier indicator -- proving the registry swap works, not just
that it imports.
"""

import pytest
from pydantic import ValidationError

from backtester.indicators import MAX_LOOKBACK
from backtester.schema import (
    KNOWN_STRATEGIES,
    Comparison,
    Condition,
    ConstantTerm,
    IndicatorTerm,
    PriceTerm,
    ScaledTerm,
    StrategyRule,
)


def test_known_strategies_all_validate():
    """The four worked examples (including morning star, the expressiveness
    proof) all construct as valid StrategyRule instances."""
    assert set(KNOWN_STRATEGIES) == {"sma_10_30_crossover", "rsi_14_30_70", "rsi_2_10_90", "morning_star"}
    for rule in KNOWN_STRATEGIES.values():
        assert isinstance(rule, StrategyRule)


def test_rule_using_verified_extended_indicator_validates():
    """A rule referencing a verified extended-tier indicator (AROOND, from
    ta.aroon) validates -- confirms IndicatorTerm._check_indicator actually looks
    at ALL_INDICATORS (core + extended), not just CORE_INDICATORS. Before this
    component's registry.py wiring, this would raise "unknown indicator" even
    though AROOND is a real, verified entry."""
    rule = StrategyRule(
        name="extended_indicator_smoke_test",
        description="AROOND crosses above a constant threshold",
        entry=Condition(
            kind="leaf",
            comparison=Comparison(
                left=IndicatorTerm(name="AROOND", params={"length": 14}),
                op="crosses_above",
                right=ConstantTerm(value=70),
            ),
        ),
        exit_after_bars=5,
    )
    assert rule.entry.comparison.left.name == "AROOND"


def _leaf(**kwargs) -> Condition:
    return Condition(kind="leaf", comparison=Comparison(**kwargs))


class TestIndicatorTermValidation:
    def test_unknown_indicator_rejected(self):
        with pytest.raises(ValidationError, match="unknown indicator"):
            IndicatorTerm(name="NOT_A_REAL_INDICATOR")

    def test_unverified_indicator_rejected(self):
        """Any extended-tier entry the verify script rejected must still raise --
        proves "unverified means unusable" holds for real rejected entries, not
        just a hypothetical one."""
        from backtester.registry import ALL_INDICATORS

        unverified = next(name for name, spec in ALL_INDICATORS.items() if not spec.verified)
        with pytest.raises(ValidationError, match="not verified"):
            IndicatorTerm(name=unverified)

    def test_out_of_range_param_rejected(self):
        with pytest.raises(ValidationError, match="out of bounds"):
            IndicatorTerm(name="RSI", params={"length": 10000})

    def test_unknown_param_rejected(self):
        with pytest.raises(ValidationError, match="has no param"):
            IndicatorTerm(name="RSI", params={"not_a_real_param": 5})

    def test_macd_cross_check_rejects_fast_gte_slow(self):
        with pytest.raises(ValidationError, match="must be <"):
            IndicatorTerm(name="MACD", params={"fast": 26, "slow": 12, "signal": 9})

    def test_macd_cross_check_accepts_fast_lt_slow(self):
        term = IndicatorTerm(name="MACD", params={"fast": 12, "slow": 26, "signal": 9})
        assert term.params == {"fast": 12, "slow": 26, "signal": 9}

    def test_positive_offset_rejected(self):
        with pytest.raises(ValidationError, match="lookahead"):
            IndicatorTerm(name="RSI", offset=1)

    def test_offset_beyond_max_lookback_rejected(self):
        with pytest.raises(ValidationError):
            IndicatorTerm(name="RSI", offset=-(MAX_LOOKBACK + 1))

    def test_offset_at_max_lookback_accepted(self):
        term = IndicatorTerm(name="RSI", offset=-MAX_LOOKBACK)
        assert term.offset == -MAX_LOOKBACK


class TestScaledTermValidation:
    def test_nested_scaled_term_rejected(self):
        with pytest.raises(ValidationError, match="no nesting"):
            ScaledTerm(term=ScaledTerm(term=PriceTerm(field="close"), factor=2.0), factor=3.0)

    def test_non_positive_factor_rejected(self):
        with pytest.raises(ValidationError, match="positive and finite"):
            ScaledTerm(term=PriceTerm(field="close"), factor=0.0)

    def test_valid_scaled_term_accepted(self):
        term = ScaledTerm(term=PriceTerm(field="close"), factor=2.0)
        assert term.factor == 2.0


class TestComparisonValidation:
    def test_eq_within_without_tolerance_rejected(self):
        with pytest.raises(ValidationError, match="requires tolerance"):
            Comparison(left=PriceTerm(field="close"), op="eq_within", right=ConstantTerm(value=100))

    def test_tolerance_on_non_eq_within_rejected(self):
        with pytest.raises(ValidationError, match="only valid for eq_within"):
            Comparison(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=100), tolerance=1.0)

    def test_eq_within_with_tolerance_accepted(self):
        cmp = Comparison(left=PriceTerm(field="close"), op="eq_within", right=ConstantTerm(value=100), tolerance=0.5)
        assert cmp.tolerance == 0.5


class TestConditionValidation:
    def test_leaf_with_children_rejected(self):
        with pytest.raises(ValidationError, match="must not have children"):
            Condition(
                kind="leaf",
                comparison=Comparison(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=100)),
                children=[_leaf(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=1))],
            )

    def test_leaf_without_comparison_rejected(self):
        with pytest.raises(ValidationError, match="requires a comparison"):
            Condition(kind="leaf")

    def test_and_node_without_children_rejected(self):
        with pytest.raises(ValidationError, match="requires at least 2 children"):
            Condition(kind="and", children=[])

    def test_and_node_with_one_child_rejected(self):
        with pytest.raises(ValidationError, match="requires at least 2 children"):
            Condition(kind="and", children=[_leaf(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=1))])

    def test_and_node_with_comparison_rejected(self):
        with pytest.raises(ValidationError, match="must not have a comparison"):
            Condition(
                kind="and",
                comparison=Comparison(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=100)),
                children=[
                    _leaf(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=1)),
                    _leaf(left=PriceTerm(field="close"), op="lt", right=ConstantTerm(value=2)),
                ],
            )


class TestStrategyRuleValidation:
    def _entry(self) -> Condition:
        return _leaf(left=PriceTerm(field="close"), op="gt", right=ConstantTerm(value=100))

    def test_no_exit_and_no_exit_after_bars_rejected(self):
        with pytest.raises(ValidationError, match="requires exit and/or exit_after_bars"):
            StrategyRule(name="x", description="x", entry=self._entry())

    def test_exit_after_bars_zero_rejected(self):
        with pytest.raises(ValidationError, match="must be positive"):
            StrategyRule(name="x", description="x", entry=self._entry(), exit_after_bars=0)

    def test_exit_only_accepted(self):
        rule = StrategyRule(name="x", description="x", entry=self._entry(), exit=self._entry())
        assert rule.exit is not None

    def test_exit_after_bars_only_accepted(self):
        rule = StrategyRule(name="x", description="x", entry=self._entry(), exit_after_bars=3)
        assert rule.exit_after_bars == 3
