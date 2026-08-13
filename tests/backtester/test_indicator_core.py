"""Tests for indicators.py's core registry.

Formal automated coverage for Components 2-7, written as part of Component 8
(extended indicator generation) rather than deferred: this component edits
_infer_inputs (the open_ alias fix) and the registry lookup schema.py depends on,
so the already-working core registry needs regression protection before those
edits ship, not after.
"""

import math

import pandas as pd
import pytest

from backtester.indicators import CORE_INDICATORS, _infer_inputs, normalize_params, select_output_column


@pytest.fixture
def lowercase_data(synthetic_data: pd.DataFrame) -> pd.DataFrame:
    """The shared 500-bar synthetic_data fixture, columns renamed to the lowercase
    field names pandas-ta functions expect when called directly (not through
    backtesting.py's capitalized Open/High/Low/Close convention)."""
    return synthetic_data.rename(columns=str.lower)


def _series_for(name: str, spec, data: pd.DataFrame, params: dict[str, float]) -> pd.Series:
    args = [data[field] for field in spec.inputs]
    result = spec.fn(*args, **normalize_params(params))
    assert result is not None, f"{name}: pandas-ta returned None"
    return select_output_column(result, spec.column_prefix)


@pytest.mark.parametrize("name", sorted(CORE_INDICATORS))
def test_core_indicator_runs_at_min_bound_with_signal(name, lowercase_data):
    """At its declared MINIMUM params, every core indicator must execute and
    produce at least one non-NaN value after warmup on the standard 500-bar
    fixture -- a small window always has plenty of warmup room, so there's no
    legitimate reason for this to fail."""
    spec = CORE_INDICATORS[name]
    params = {p: bounds[0] for p, bounds in spec.params.items()}
    series = _series_for(name, spec, lowercase_data, params)
    arr = series.to_numpy(dtype=float)
    tail = arr[-50:] if len(arr) >= 50 else arr
    assert any(math.isfinite(v) for v in tail), f"{name}: no finite values in the tail at params={params}"


@pytest.mark.parametrize("name", sorted(CORE_INDICATORS))
def test_core_indicator_does_not_raise_at_max_bound(name, lowercase_data):
    """At its declared MAXIMUM params, every core indicator must execute without
    raising -- but is NOT required to produce a signal. A large window (e.g.
    length=200, a completely standard real-world setting) can legitimately need
    more history than a 500-bar fixture provides; that's a property of the data
    size, not a broken indicator. Confirmed for real: TEMA at length=200 returns
    None on 500 bars (a triple-smoothed MA needs roughly 3x the window to warm
    up) -- a clean None is an accepted outcome here, an exception is not.
    """
    spec = CORE_INDICATORS[name]
    params = {p: bounds[1] for p, bounds in spec.params.items()}
    args = [lowercase_data[field] for field in spec.inputs]
    result = spec.fn(*args, **normalize_params(params))
    if result is not None:
        select_output_column(result, spec.column_prefix)  # shape must still be sane if it did return


@pytest.mark.parametrize("name", sorted(CORE_INDICATORS))
def test_core_indicator_column_prefix_matches_exactly_one_column(name, lowercase_data):
    """DataFrame-returning indicators' column_prefix must resolve to exactly one
    column -- select_output_column raises otherwise, which this simply confirms
    doesn't happen for any core entry at its own declared default/min params."""
    spec = CORE_INDICATORS[name]
    params = {p: bounds[0] for p, bounds in spec.params.items()}
    args = [lowercase_data[field] for field in spec.inputs]
    result = spec.fn(*args, **normalize_params(params))
    select_output_column(result, spec.column_prefix)  # raises on ambiguous/missing match


def test_infer_inputs_resolves_pandas_ta_open_alias():
    """Regression test for a real, previously-undetected bug: pandas-ta names its
    "open" parameter `open_` (dodging the Python builtin). Before this alias was
    added, _infer_inputs silently reported zero required inputs for any function
    needing open price, and the function would blow up with "missing required
    positional argument" the moment it was actually called. No core indicator
    happens to need open price, so this was invisible until the extended-indicator
    sweep exercised a function that does (ta.ha, Heikin-Ashi candles).
    """
    import pandas_ta as ta

    assert _infer_inputs(ta.ha) == ("open", "high", "low", "close")
