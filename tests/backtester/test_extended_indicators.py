"""Minimal formal coverage for the extended-indicator verification pipeline.

Deliberately narrow, not the full plan §8 suite: this ships alongside Component 8
(extended indicator generation) because it's the direct regression protection for
the NEW code this component adds (scripts/verify_extended_indicators.py's checks).
The broader structural-validity cases plan §8 describes for
test_extended_indicators.py (generated stubs are structurally valid, provenance
flag population, etc.) are deferred to that dedicated pass -- provenance
population is already covered manually in this component's own verification step,
and stub structure is exercised for real every time generate_extended_indicators.py
runs, which it already has, against the real registry.

The one thing that must be proven here, and proven both directions (the same
true-positive/true-negative standard the morning-star gate case uses, and the
standard the Component 5 dedup test needed a second pass to actually meet): the
verifier rejects a genuinely broken spec AND accepts a genuinely valid one. Both
specs below are built from REAL, already-empirically-confirmed pandas-ta behavior
(not a synthetic dummy function), for the strongest grounding available: cksp's
`q` parameter was independently confirmed dead during this component's own
exploration (varying it produces no change in cksp's output on this pandas-ta
version), and aroon's length/scalar were independently confirmed sensitive.
"""

import sys
from pathlib import Path

import pandas_ta as ta

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from backtester.indicators import IndicatorSpec  # noqa: E402
from _extended_codegen import make_synthetic_ohlcv  # noqa: E402
from verify_extended_indicators import verify_one  # noqa: E402


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
