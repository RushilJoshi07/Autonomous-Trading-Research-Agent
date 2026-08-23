"""Compiles a validated StrategyRule into a real backtesting.py Strategy subclass.

make_rule_strategy(rule) -> type[Strategy] is the Stage 3 payoff: any of the four
KNOWN_STRATEGIES (or any future rule) can be run through Stage 2's run_backtest
with zero strategy-specific Python code. Scope: long-only, single position, full
allocation — matching Stage 2's exclusive_orders=True and the plan's documented
boundary.
"""

from __future__ import annotations

from backtesting import Strategy

from ..evaluator import BarContext, IndicatorKey, evaluate_condition, indicator_key
from ..indicators import normalize_params, select_output_column
from ..registry import ALL_INDICATORS
from ..schema import Condition, IndicatorTerm, ScaledTerm, StrategyRule, Term

_FIELD_TO_ATTR = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}


def _collect_from_term(term: Term) -> list[IndicatorTerm]:
    if isinstance(term, IndicatorTerm):
        return [term]
    if isinstance(term, ScaledTerm):
        return _collect_from_term(term.term)
    return []


def _collect_indicator_terms(cond: Condition) -> list[IndicatorTerm]:
    """Every IndicatorTerm referenced anywhere in a condition tree, duplicates included."""
    if cond.kind == "leaf":
        assert cond.comparison is not None
        return _collect_from_term(cond.comparison.left) + _collect_from_term(cond.comparison.right)
    assert cond.children is not None
    found: list[IndicatorTerm] = []
    for child in cond.children:
        found.extend(_collect_indicator_terms(child))
    return found


class _RuleBarContext:
    """BarContext backed by a live Strategy instance's arrays.

    Schema offset k maps to array index k - 1 inside next() (Stage 3 plan's own
    empirical finding) — backtesting.py's _Array supports negative indexing from
    "the current bar."

    Critical, non-obvious mechanic (verified against backtesting.py's own source,
    not assumed): the array returned by self.I() does NOT carry live per-bar
    slicing as an intrinsic property of the object. backtesting.py's own run loop
    re-slices it every bar via `setattr(strategy, attr, indicator[..., :i+1])`,
    but ONLY for objects it discovers as direct, top-level attributes on the
    Strategy instance (`_strategy_indicators` scans `strategy.__dict__` for
    `isinstance(v, _Indicator)`). An indicator stored inside a dict — even the
    exact same object — is invisible to that scan and is never re-sliced; every
    later read silently returns the full, final-length array from init(), not
    the array truncated to the current bar. So each precomputed indicator here
    is stored as its own named attribute (self._ind_0, self._ind_1, ...), and
    read back via getattr() so this class always sees whatever backtesting.py
    most recently wrote to that attribute name.
    """

    def __init__(self, strategy: Strategy) -> None:
        self._strategy = strategy

    def price(self, field: str, offset: int) -> float:
        return self._at(getattr(self._strategy.data, _FIELD_TO_ATTR[field]), offset)

    def indicator(self, key: IndicatorKey, offset: int) -> float:
        attr_name = self._strategy._key_to_attr[key]
        return self._at(getattr(self._strategy, attr_name), offset)

    @staticmethod
    def _at(arr, offset: int) -> float:
        try:
            return float(arr[offset - 1])
        except IndexError:
            return float("nan")


def unique_terms(*term_lists: list[IndicatorTerm]) -> dict[IndicatorKey, IndicatorTerm]:
    """Dedup any number of IndicatorTerm lists into one key->term map, first occurrence wins."""
    result: dict[IndicatorKey, IndicatorTerm] = {}
    for terms in term_lists:
        for term in terms:
            result.setdefault(indicator_key(term), term)
    return result


def rule_indicator_names(rule: StrategyRule) -> tuple[str, ...]:
    """Every distinct indicator name referenced anywhere in a rule's entry or
    exit tree, sorted. Public because Stage 5's execution loop
    (agentic_core/loop_state.py) constrains the agent's compute_indicator
    calls to exactly this set -- the diagnostic question the loop can ask is
    "why did THIS rule behave this way", so the indicators it may inspect are
    the ones the rule itself uses. Returns a tuple, not a list, because it
    feeds a typing.Literal, which requires hashable arguments.
    """
    entry_terms = _collect_indicator_terms(rule.entry)
    exit_terms = _collect_indicator_terms(rule.exit) if rule.exit is not None else []
    terms = unique_terms(entry_terms, exit_terms)
    return tuple(sorted({term.name for term in terms.values()}))


def indicator_usage(terms: dict[IndicatorKey, IndicatorTerm]) -> tuple[list[str], list[str]]:
    """Split a unique-terms map into (core names used, extended names used), both sorted."""
    used_names = {term.name for term in terms.values()}
    indicators_used = sorted(n for n in used_names if ALL_INDICATORS[n].tier == "core")
    extended_indicators_used = sorted(n for n in used_names if ALL_INDICATORS[n].tier == "extended")
    return indicators_used, extended_indicators_used


def wire_indicators(strategy: Strategy, terms: dict[IndicatorKey, IndicatorTerm]) -> dict[IndicatorKey, str]:
    """Set up self.I()-computed indicator arrays on strategy for each unique term.

    Returns the key->attribute-name map _RuleBarContext.indicator() needs to read them
    back. See _RuleBarContext's own docstring for why each one gets a named attribute
    rather than living in a dict.
    """
    key_to_attr: dict[IndicatorKey, str] = {}
    for i, (key, term) in enumerate(terms.items()):
        spec = ALL_INDICATORS[term.name]
        price_args = [getattr(strategy.data, _FIELD_TO_ATTR[field]).s for field in spec.inputs]

        def _compute(*args, _spec=spec, _name=term.name, **kwargs):
            result = _spec.fn(*args, **kwargs)
            if result is None:
                raise ValueError(
                    f"{_name}: pandas-ta returned None — check inputs "
                    f"(e.g. a required DatetimeIndex)"
                )
            try:
                return select_output_column(result, _spec.column_prefix)
            except ValueError as e:
                raise ValueError(f"{_name}: {e}") from e

        attr_name = f"_ind_{i}"
        setattr(strategy, attr_name, strategy.I(_compute, *price_args, **normalize_params(term.params)))
        key_to_attr[key] = attr_name
    return key_to_attr


def apply_exit(strategy: Strategy, rule: StrategyRule, ctx: BarContext) -> None:
    """Close strategy's position if rule's exit condition or exit_after_bars fires.

    No-op if not currently in a position — callers are expected to check that first
    (entry logic is each strategy's own concern; this is only ever the shared half).
    """
    if rule.exit is not None and evaluate_condition(rule.exit, ctx):
        strategy.position.close()
        return

    if rule.exit_after_bars is not None and strategy.trades:
        bars_held = (len(strategy.data) - 1) - strategy.trades[-1].entry_bar
        if bars_held >= rule.exit_after_bars:
            strategy.position.close()


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

    RuleStrategy.__name__ = f"RuleStrategy_{rule.name}"
    RuleStrategy.__qualname__ = RuleStrategy.__name__
    RuleStrategy.indicators_used = indicators_used
    RuleStrategy.extended_indicators_used = extended_indicators_used
    return RuleStrategy
