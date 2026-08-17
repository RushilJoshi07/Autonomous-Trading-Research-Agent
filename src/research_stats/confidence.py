"""Bootstrap confidence intervals over an arbitrary list of values.

Deliberately generic — knows nothing about StrategyRule or backtesting.
Callers (the MCP tool wrapper, for the trade-level case this stage actually
needs) are responsible for deciding what values to pass in; resampling at
the trade level rather than the daily-bar level (docs/plans/stage-4-plan.md
"Decision 1") is a caller decision, not something this function enforces.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
from pydantic import BaseModel
from scipy.stats import bootstrap


class ConfidenceIntervalResult(BaseModel):
    point_estimate: float
    confidence_level: float
    low: float
    high: float
    n: int


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[np.ndarray], float] = np.mean,
    confidence_level: float = 0.95,
    seed: int = 0,
) -> ConfidenceIntervalResult:
    """Bootstrap a confidence interval for `statistic` over `values`.

    Raises ValueError if values has fewer than 2 elements — scipy.stats.bootstrap's
    own requirement, since a single-element resample carries no variance to estimate.
    """
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        raise ValueError(f"bootstrap_ci needs at least 2 values, got {arr.size}")

    result = bootstrap(
        (arr,),
        statistic,
        confidence_level=confidence_level,
        method="BCa",
        rng=np.random.default_rng(seed),
    )
    return ConfidenceIntervalResult(
        point_estimate=float(statistic(arr)),
        confidence_level=confidence_level,
        low=float(result.confidence_interval.low),
        high=float(result.confidence_interval.high),
        n=arr.size,
    )
