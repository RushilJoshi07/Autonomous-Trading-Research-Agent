"""Execute-and-check verification for extended-tier indicator candidates.

Everything scripts/generate_extended_indicators.py wrote is verified=False and
untrusted until this script runs it for real. Four checks per entry:

1. Executes at declared min and max bounds without raising; DataFrame outputs
   match their column_prefix to exactly 1 column at both endpoints.
2. Per-parameter sensitivity: each declared param, varied alone, must actually
   change the output (the bbands `std` lesson -- pandas-ta silently accepts a
   dead param without raising).
3. Cross-check claims (e.g. "fast < slow") are execution-verified too, not taken
   on the LLM's word: violating the ordering must either fail or produce
   genuinely different output from satisfying it.
4. Non-NaN values exist in the tail after warmup, at both bounds.

Entries passing all four flip to verified=True; everything else stays
verified=False, which schema.py's IndicatorTerm validator already refuses to use
-- no new logic needed for "unverified means unusable."

Run: .venv/bin/python scripts/verify_extended_indicators.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _extended_codegen import make_synthetic_ohlcv, param_midpoint, render_extended_indicators_module  # noqa: E402

from backtester.extended_indicators import EXTENDED_INDICATORS  # noqa: E402
from backtester.indicators import IndicatorSpec, normalize_params, select_output_column  # noqa: E402

LIB_VERSION = version("pandas_ta")
WARMUP_TAIL = 50


# ---------------------------------------------------------------------------
# Low-level comparisons shared by the sensitivity and cross-check tests
# ---------------------------------------------------------------------------

def _outputs_differ(a: pd.Series, b: pd.Series, atol: float = 1e-9, rtol: float = 1e-6) -> bool:
    """True if two outputs are meaningfully different. NaN-vs-NaN at the same
    position counts as agreement (both sides simply lack a value there, e.g.
    during warmup), not a difference -- otherwise every comparison involving
    warmup NaNs would trivially read as "different" regardless of the real values."""
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    if arr_a.shape != arr_b.shape:
        return True
    return not np.allclose(arr_a, arr_b, atol=atol, rtol=rtol, equal_nan=True)


def _has_non_nan_after_warmup(series: pd.Series, tail: int = WARMUP_TAIL) -> bool:
    arr = np.asarray(series, dtype=float)
    tail_vals = arr[-tail:] if len(arr) >= tail else arr
    return bool(np.isfinite(tail_vals).any())


def _run(spec: IndicatorSpec, data: pd.DataFrame, params: dict[str, float]) -> pd.Series:
    args = [data[field] for field in spec.inputs]
    result = spec.fn(*args, **normalize_params(params))
    if result is None:
        raise ValueError("returned None")
    if not isinstance(result, (pd.Series, pd.DataFrame)):
        # Defense in depth: generation already excludes non-Series/DataFrame
        # returns (e.g. ichimoku's tuple[DataFrame, DataFrame]), but a spec could
        # in principle behave differently at a param combination generation never
        # sampled -- fail this as a normal rejection, not an unhandled crash.
        raise ValueError(f"unsupported return type {type(result).__name__} (expected Series or DataFrame)")
    return select_output_column(result, spec.column_prefix)


# ---------------------------------------------------------------------------
# Check 3 support: constructing a genuine satisfied/violated pair of orderings
# ---------------------------------------------------------------------------

def _cross_check_orderings(
    cross_check: dict, params: dict[str, tuple[float, float]]
) -> tuple[dict[str, float], dict[str, float] | None]:
    """(satisfied_values, violated_values) for the cross_check's two params, or
    (satisfied_values, None) if no violating combination exists within the
    declared bounds -- meaning the constraint is structurally guaranteed by the
    bounds themselves (e.g. left's whole range sits below right's whole range),
    which is a stronger guarantee than an execution test could give anyway."""
    l, r = cross_check["left"], cross_check["right"]
    l_lo, l_hi = params[l]
    r_lo, r_hi = params[r]
    t = cross_check["type"]

    if t == "less_than":
        satisfied = {l: l_lo, r: r_hi}
        violated = {l: l_hi, r: r_lo} if l_hi >= r_lo else None
    elif t == "greater_than":
        satisfied = {l: l_hi, r: r_lo}
        violated = {l: l_lo, r: r_hi} if l_lo <= r_hi else None
    else:  # not_equal
        satisfied = {l: l_lo, r: r_hi} if l_lo != r_hi else {l: l_lo, r: r_lo}
        overlap_lo, overlap_hi = max(l_lo, r_lo), min(l_hi, r_hi)
        violated = {l: overlap_lo, r: overlap_lo} if overlap_lo <= overlap_hi else None

    return satisfied, violated


# ---------------------------------------------------------------------------
# The four checks
# ---------------------------------------------------------------------------

def verify_execution_and_shape(spec: IndicatorSpec, data: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    min_params = {p: lo for p, (lo, hi) in spec.params.items()}
    max_params = {p: hi for p, (lo, hi) in spec.params.items()}
    return _run(spec, data, min_params), _run(spec, data, max_params)


def verify_non_nan(series_min: pd.Series, series_max: pd.Series) -> tuple[bool, str | None]:
    if not (_has_non_nan_after_warmup(series_min) and _has_non_nan_after_warmup(series_max)):
        return False, f"no non-NaN values in the last {WARMUP_TAIL} bars at declared min or max bounds"
    return True, None


def verify_sensitivity(spec: IndicatorSpec, data: pd.DataFrame) -> tuple[bool, str | None]:
    if not spec.params:
        return True, None
    baseline = {p: param_midpoint(b) for p, b in spec.params.items()}
    for pname, (lo, hi) in spec.params.items():
        try:
            series_lo = _run(spec, data, {**baseline, pname: lo})
            series_hi = _run(spec, data, {**baseline, pname: hi})
        except Exception as e:  # noqa: BLE001 -- probing execution, any failure is a real verification result
            return False, f"execution failed while varying {pname!r} ({lo} vs {hi}): {e!r}"
        if not _outputs_differ(series_lo, series_hi):
            return False, f"varying {pname!r} between {lo} and {hi} produced no change in output (dead param)"
    return True, None


def verify_cross_check(spec: IndicatorSpec, data: pd.DataFrame) -> tuple[bool, str | None]:
    if spec.cross_check is None:
        return True, None
    satisfied, violated = _cross_check_orderings(spec.cross_check, spec.params)
    if violated is None:
        return True, None  # structurally guaranteed by the bounds themselves

    baseline = {p: param_midpoint(b) for p, b in spec.params.items()}
    try:
        series_satisfied = _run(spec, data, {**baseline, **satisfied})
    except Exception as e:  # noqa: BLE001
        return False, f"cross_check {spec.cross_check!r}: execution at the satisfied ordering raised: {e!r}"

    try:
        series_violated = _run(spec, data, {**baseline, **violated})
    except Exception:  # noqa: BLE001 -- violating the ordering breaking the function IS the constraint being real
        return True, None

    if not _outputs_differ(series_satisfied, series_violated):
        return False, (
            f"cross_check {spec.cross_check!r} unverified: violating the ordering executed cleanly and "
            f"produced output indistinguishable from the satisfied ordering -- the claimed constraint "
            f"isn't load-bearing"
        )
    return True, None


def verify_one(spec: IndicatorSpec, data: pd.DataFrame) -> tuple[bool, str | None]:
    try:
        series_min, series_max = verify_execution_and_shape(spec, data)
    except Exception as e:  # noqa: BLE001
        return False, f"execution at declared bounds raised: {e!r}"

    for check in (
        lambda: verify_non_nan(series_min, series_max),
        lambda: verify_sensitivity(spec, data),
        lambda: verify_cross_check(spec, data),
    ):
        ok, reason = check()
        if not ok:
            return False, reason

    return True, None


def main() -> None:
    data = make_synthetic_ohlcv()

    updated: dict[str, IndicatorSpec] = {}
    rejected: dict[str, str] = {}
    verified_count = 0
    already_verified_count = 0

    for name, spec in EXTENDED_INDICATORS.items():
        if spec.verified:
            updated[name] = spec
            already_verified_count += 1
            continue

        try:
            ok, reason = verify_one(spec, data)
        except Exception as e:  # noqa: BLE001 -- one entry's unexpected failure must not abort the whole run
            ok, reason = False, f"verification raised an unexpected exception: {e!r}"

        if ok:
            updated[name] = replace(spec, verified=True, verified_on=date.today(), lib_version=LIB_VERSION)
            verified_count += 1
        else:
            updated[name] = spec
            rejected[name] = reason or "unknown"

    out_path = Path(__file__).resolve().parent.parent / "src" / "backtester" / "extended_indicators.py"
    out_path.write_text(render_extended_indicators_module(updated))

    total = len(EXTENDED_INDICATORS)
    print(f"Verified this run: {verified_count}/{total - already_verified_count} candidates")
    if already_verified_count:
        print(f"Already verified from a previous run (left unchanged): {already_verified_count}")
    print(f"Rejected: {len(rejected)}")
    for name, reason in sorted(rejected.items()):
        print(f"  {name}: {reason}")
    print(f"\nRewrote {out_path}")


if __name__ == "__main__":
    main()
