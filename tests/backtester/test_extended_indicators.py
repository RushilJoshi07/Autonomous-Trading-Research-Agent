"""Formal coverage for the extended-indicator tier: generation output, the
verification pipeline, and the schema/rule_strategy contract that governs it.

The adversarial verifier tests below (rejects broken, accepts valid) shipped
with Component 8 itself, minimal by design at the time -- the rest of this file
is the dedicated plan §8 pass that fills in the remaining cases: the real
generated file's own structural sanity, "unverified means unusable" for a real
rejected extended entry, and provenance population on a real backtest result.

The adversarial pair must be proven both directions (the same true-positive/
true-negative standard the morning-star gate case uses, and the standard the
Component 5 dedup test needed a second pass to actually meet): the verifier
rejects a genuinely broken spec AND accepts a genuinely valid one. Both specs
below are built from REAL, already-empirically-confirmed pandas-ta behavior
(not a synthetic dummy function), for the strongest grounding available: cksp's
`q` parameter was independently confirmed dead during this component's own
exploration (varying it produces no change in cksp's output on this pandas-ta
version), and aroon's length/scalar were independently confirmed sensitive.
"""

import sys
from pathlib import Path

import pandas_ta as ta
import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from backtester.engine import run_backtest  # noqa: E402
from backtester.extended_indicators import EXTENDED_INDICATORS  # noqa: E402
from backtester.indicators import IndicatorSpec  # noqa: E402
from backtester.schema import Comparison, Condition, ConstantTerm, IndicatorTerm, StrategyRule  # noqa: E402
from backtester.strategies.rule_strategy import make_rule_strategy  # noqa: E402
from _extended_codegen import make_synthetic_ohlcv  # noqa: E402
from verify_extended_indicators import verify_one  # noqa: E402

_VALID_OHLCV_FIELDS = {"open", "high", "low", "close", "volume"}


def test_verifier_rejects_a_genuinely_dead_param():
    """cksp's `q` parameter does not affect cksp's output on this library version
    (confirmed independently during this component's exploration). A spec that
    claims it as a tunable bound must be rejected by the sensitivity check."""
    spec = IndicatorSpec(
        fn=ta.cksp,
        inputs=("high", "low", "close"),
        params={"p": (5.0, 100.0), "x": (0.1, 10.0), "q": (1.0, 50.0)},
        column_prefix="CKSPl_",
        tier="extended",
        verified=False,
    )
    ok, reason = verify_one(spec, make_synthetic_ohlcv())
    assert ok is False
    assert "q" in reason
    assert "dead param" in reason


def test_verifier_accepts_a_genuinely_valid_indicator():
    """aroon's length and scalar are both real, load-bearing parameters
    (confirmed independently during this component's exploration). A correctly
    specified spec must pass every check and be accepted.

    This is the other half of the standard: (a) alone (the rejection test above)
    only proves the verifier CAN say no -- an overly strict verifier that rejects
    everything would pass that test too, while being useless. This confirms it
    also says yes to something that deserves it.
    """
    spec = IndicatorSpec(
        fn=ta.aroon,
        inputs=("high", "low"),
        params={"length": (2.0, 100.0), "scalar": (1.0, 100.0)},
        column_prefix="AROOND_",
        tier="extended",
        verified=False,
    )
    ok, reason = verify_one(spec, make_synthetic_ohlcv())
    assert ok is True
    assert reason is None


def test_generated_stubs_are_structurally_valid():
    """Sanity net on the real, checked-in generated file -- imports the ACTUAL
    EXTENDED_INDICATORS dict as it exists on disk right now, not a fixture. A
    fixture could drift from what generate_extended_indicators.py actually
    produces the next time it's rerun (a different pandas-ta version, a
    different LLM response, a newly-added core indicator changing what's
    excluded); testing the real dict means this re-validates automatically on
    every regeneration, with nothing separate to keep in sync."""
    assert len(EXTENDED_INDICATORS) > 0, "expected the real generated file to contain candidates"
    for name, spec in EXTENDED_INDICATORS.items():
        assert spec.tier == "extended", f"{name}: tier should be 'extended'"
        assert spec.inputs, f"{name}: inputs must not be empty"
        assert set(spec.inputs) <= _VALID_OHLCV_FIELDS, f"{name}: invalid OHLCV field in {spec.inputs}"
        assert callable(spec.fn), f"{name}: fn must be callable"
        for pname, bounds in spec.params.items():
            lo, hi = bounds
            assert lo < hi, f"{name}.{pname}: bounds {bounds} must have min < max"


def test_rule_using_unverified_extended_indicator_fails_validation():
    """"Unverified means unusable" specifically for the extended tier, using a
    real rejected entry picked from the real registry at test time -- not a
    hand-constructed unverified spec. Mirrors test_schema.py's
    test_unverified_indicator_rejected, scoped here because it's specifically
    about the extended tier's own contract, not schema.py's validator in the
    abstract."""
    unverified_name = next(name for name, spec in EXTENDED_INDICATORS.items() if not spec.verified)
    with pytest.raises(ValidationError, match="not verified"):
        IndicatorTerm(name=unverified_name)


def test_verified_extended_indicator_populates_provenance_on_result(synthetic_data):
    """Formalizes the ad hoc end-to-end check already run manually during
    Component 8 into a permanent regression test: a rule using a verified
    extended-tier indicator must produce a BacktestResult with
    extended_indicators_used populated. This is the exact gap that made
    extended indicators structurally unusable before registry.py's
    ALL_INDICATORS wiring existed (rule_strategy.py indexed CORE_INDICATORS
    directly and would KeyError on any extended-tier name) -- if that wiring
    ever regresses, this test catches it, not just a manual re-check."""
    verified_name = next(name for name, spec in EXTENDED_INDICATORS.items() if spec.verified)
    rule = StrategyRule(
        name="extended_provenance_smoke_test",
        description=f"{verified_name} greater than an always-true threshold",
        entry=Condition(
            kind="leaf",
            comparison=Comparison(
                left=IndicatorTerm(name=verified_name), op="gt", right=ConstantTerm(value=-1e9)
            ),
        ),
        exit_after_bars=5,
    )
    strategy_cls = make_rule_strategy(rule)
    result = run_backtest(synthetic_data, strategy_cls, ticker="SYNTHETIC")
    assert result.extended_indicators_used == [verified_name]
    assert result.indicators_used == []
