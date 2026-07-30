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
from ..indicators import CORE_INDICATORS
from ..schema import Condition, IndicatorTerm, ScaledTerm, StrategyRule, Term

_FIELD_TO_ATTR = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}


def _normalize_params(params: dict[str, float]) -> dict[str, float | int]:
    """Whole-valued floats become int; e.g. length=10.0 -> 10.

    IndicatorTerm.params is typed as dict[str, float] for uniform bounds-checking
    in schema.py, but some pandas-ta indicators (e.g. sma's numba-jitted path)
    require a genuine int for bar-count params and raise a numba TypingError on
    a float. Verified safe for non-integer params too (e.g. bbands' lower_std):
    a whole-valued float and the equivalent int produce numerically identical
    output, differing only in cosmetic column-name formatting ("1.0" vs "1"),
    which column_prefix matching (prefix-only) is already indifferent to.
    """
    return {k: (int(v) if float(v).is_integer() else v) for k, v in params.items()}


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


def make_rule_strategy(rule: StrategyRule) -> type[Strategy]:
    entry_terms = _collect_indicator_terms(rule.entry)
    exit_terms = _collect_indicator_terms(rule.exit) if rule.exit is not None else []

    unique_terms: dict[IndicatorKey, IndicatorTerm] = {}
    for term in entry_terms + exit_terms:
        unique_terms.setdefault(indicator_key(term), term)

    used_names = {term.name for term in unique_terms.values()}
    indicators_used = sorted(n for n in used_names if CORE_INDICATORS[n].tier == "core")
    extended_indicators_used = sorted(n for n in used_names if CORE_INDICATORS[n].tier == "extended")

    class RuleStrategy(Strategy):
        def init(self) -> None:
            self._key_to_attr: dict[IndicatorKey, str] = {}
            for i, (key, term) in enumerate(unique_terms.items()):
                spec = CORE_INDICATORS[term.name]
                price_args = [getattr(self.data, _FIELD_TO_ATTR[field]).s for field in spec.inputs]

                def _compute(*args, _spec=spec, _name=term.name, **kwargs):
                    result = _spec.fn(*args, **kwargs)
                    if result is None:
                        raise ValueError(
                            f"{_name}: pandas-ta returned None — check inputs "
                            f"(e.g. a required DatetimeIndex)"
                        )
                    if _spec.column_prefix:
                        cols = [c for c in result.columns if c.startswith(_spec.column_prefix)]
                        if len(cols) != 1:
                            raise ValueError(
                                f"{_name}: column_prefix {_spec.column_prefix!r} matched "
                                f"{len(cols)} columns, expected exactly 1"
                            )
                        result = result[cols[0]]
                    return result

                attr_name = f"_ind_{i}"
                setattr(self, attr_name, self.I(_compute, *price_args, **_normalize_params(term.params)))
                self._key_to_attr[key] = attr_name

            self._ctx: BarContext = _RuleBarContext(self)

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

    RuleStrategy.__name__ = f"RuleStrategy_{rule.name}"
    RuleStrategy.__qualname__ = RuleStrategy.__name__
    RuleStrategy.indicators_used = indicators_used
    RuleStrategy.extended_indicators_used = extended_indicators_used
    return RuleStrategy
