"""Tests for research_stats/confidence.py -- bootstrap confidence intervals.

Component 8 (Stage 4) formal coverage for Component 6.
"""

import numpy as np
import pytest

from research_stats.confidence import bootstrap_ci


def test_ci_brackets_the_point_estimate():
    rng = np.random.default_rng(0)
    values = rng.normal(0.03, 0.05, 50).tolist()
    result = bootstrap_ci(values, seed=1)
    assert result.low < result.point_estimate < result.high
    assert result.n == 50
    assert result.confidence_level == 0.95


def test_fewer_than_two_values_raises():
    with pytest.raises(ValueError, match="at least 2"):
        bootstrap_ci([0.05])
    with pytest.raises(ValueError, match="at least 2"):
        bootstrap_ci([])


def test_same_seed_gives_identical_ci():
    rng = np.random.default_rng(0)
    values = rng.normal(0.02, 0.04, 40).tolist()
    a = bootstrap_ci(values, seed=3)
    b = bootstrap_ci(values, seed=3)
    assert a.low == b.low
    assert a.high == b.high


def test_wider_confidence_level_gives_wider_interval():
    """A 99% CI must be at least as wide as a 95% CI on the same data --
    the basic monotonicity property any correct interval estimate must
    have, checked directly rather than assumed from calling scipy correctly."""
    rng = np.random.default_rng(0)
    values = rng.normal(0.03, 0.05, 60).tolist()
    ci_95 = bootstrap_ci(values, confidence_level=0.95, seed=2)
    ci_99 = bootstrap_ci(values, confidence_level=0.99, seed=2)
    assert (ci_99.high - ci_99.low) >= (ci_95.high - ci_95.low)
