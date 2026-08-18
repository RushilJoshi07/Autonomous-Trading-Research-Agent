"""Tests for research_stats/multiple_comparisons.py -- Benjamini-Hochberg
p-value correction.

Component 8 (Stage 4) formal coverage for Component 6.
"""

from research_stats.multiple_comparisons import correct_p_values


def test_matches_hand_computed_benjamini_hochberg():
    """Cross-check against the BH procedure computed by hand, not just
    trusting scipy's own implementation blindly -- the same "verify by an
    independent reproduction" discipline used throughout this stage.

    BH for m=4 tests: adjusted[i] = min(p[i] * m / rank[i], adjusted[i+1]),
    enforced monotonically from the largest p-value down. For sorted
    p-values [0.01, 0.02, 0.03, 0.20] (ranks 1-4, m=4):
      rank 4 (0.20): 0.20 * 4/4 = 0.20
      rank 3 (0.03): min(0.03 * 4/3, 0.20) = min(0.04, 0.20) = 0.04
      rank 2 (0.02): min(0.02 * 4/2, 0.04) = min(0.04, 0.04) = 0.04
      rank 1 (0.01): min(0.01 * 4/1, 0.04) = min(0.04, 0.04) = 0.04
    """
    result = correct_p_values([0.01, 0.02, 0.03, 0.20], method="bh")
    expected = [0.04, 0.04, 0.04, 0.20]
    for actual, exp in zip(result.adjusted_p_values, expected):
        assert abs(actual - exp) < 1e-9


def test_adjusted_values_are_monotonic_with_input_order():
    """A larger original p-value must never receive a SMALLER adjusted
    p-value than a smaller original one -- BH preserves relative order,
    even though it doesn't preserve exact ratios."""
    p_values = [0.001, 0.01, 0.03, 0.04, 0.049, 0.2, 0.5]
    result = correct_p_values(p_values)
    pairs = sorted(zip(p_values, result.adjusted_p_values))
    adjusted_in_p_order = [adj for _, adj in pairs]
    assert adjusted_in_p_order == sorted(adjusted_in_p_order)


def test_adjusted_values_never_smaller_than_original():
    p_values = [0.001, 0.01, 0.03, 0.04, 0.049, 0.2, 0.5]
    result = correct_p_values(p_values)
    for original, adjusted in zip(result.p_values, result.adjusted_p_values):
        assert adjusted >= original - 1e-12


def test_echoes_original_p_values_unchanged():
    p_values = [0.03, 0.01, 0.5]
    result = correct_p_values(p_values)
    assert result.p_values == p_values
