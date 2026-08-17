"""Multiple-comparisons correction (Benjamini-Hochberg).

The agent generates its own hypotheses, so it can test enough of them that
one passes by chance — architecture.md §5 Step 5's "biggest threat to the
honesty claim." This is the deterministic correction function; TRACKING how
many hypotheses have been tested under a charter is Stage 5's job (it needs
the agent loop's persistent state, which doesn't exist yet) — this function
is exposed now, ready for Stage 5 to call once it has a count to correct.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel
from scipy.stats import false_discovery_control


class MultipleComparisonsResult(BaseModel):
    p_values: list[float]
    adjusted_p_values: list[float]
    method: str


def correct_p_values(p_values: Sequence[float], method: str = "bh") -> MultipleComparisonsResult:
    """Adjust a list of p-values for multiple comparisons.

    method="bh" (Benjamini-Hochberg, controls false discovery rate) is the
    only method exposed for now — scipy.stats.false_discovery_control also
    supports "by" (Benjamini-Yekutieli, valid under arbitrary dependence
    between tests, more conservative); not exposed here because nothing in
    this project's design has established the tests it corrects are
    dependent in the way "by" exists to handle.
    """
    adjusted = false_discovery_control(list(p_values), method=method)
    return MultipleComparisonsResult(
        p_values=list(p_values),
        adjusted_p_values=adjusted.tolist(),
        method=method,
    )
