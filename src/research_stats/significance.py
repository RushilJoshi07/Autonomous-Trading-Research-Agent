"""Monte Carlo significance test: does a strategy beat randomized entries at
the same trade frequency? (architecture.md §5 Step 3's mandatory control.)

No distributional assumption — validity comes from the resampling procedure
(real backtests against the real, autocorrelated price path, only entry
timing randomized), not an asymptotic approximation like a t-test would
need. Full justification against the rejected alternatives (t-test,
Mann-Whitney, block bootstrap) lives in docs/plans/stage-4-plan.md
"Decision 1" — not repeated here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel
from scipy.stats import monte_carlo_test

from backtester.engine import run_backtest
from backtester.schema import StrategyRule
from backtester.strategies.random_entry_strategy import make_random_entry_strategy
from backtester.strategies.rule_strategy import make_rule_strategy

_DEFAULT_N_RESAMPLES = 300
_MAX_RETRIES_PER_DRAW = 20


class SignificanceResult(BaseModel):
    observed_sharpe: float
    observed_num_trades: int
    p_value: float
    n_resamples: int
    null_mean_sharpe: float
    null_std_sharpe: float


def test_significance(
    price_data: pd.DataFrame,
    rule: StrategyRule,
    ticker: str = "",
    commission: float | None = None,
    cash: float | None = None,
    n_resamples: int = _DEFAULT_N_RESAMPLES,
    seed: int = 0,
) -> SignificanceResult:
    """Test whether rule's real Sharpe ratio beats n_resamples randomized-entry
    controls matched on exit logic and approximate trade frequency.

    Raises ValueError if the real strategy produces 0 trades (no basis for a
    frequency-matched comparison) or if a random-entry control can't produce a
    single trade within _MAX_RETRIES_PER_DRAW attempts (observed_num_trades is
    likely too low relative to the data length for this to be meaningful).
    """
    kwargs: dict[str, float] = {}
    if commission is not None:
        kwargs["commission"] = commission
    if cash is not None:
        kwargs["cash"] = cash

    observed = run_backtest(price_data, make_rule_strategy(rule), ticker=ticker, **kwargs)
    if observed.num_trades == 0:
        raise ValueError(
            f"cannot test significance: {rule.name!r} produced 0 trades against this "
            "data — no basis for a randomized-entry comparison"
        )

    def rvs(size: tuple[int, ...] | int) -> np.ndarray:
        # monte_carlo_test calls this with size as a tuple (n_resamples, sample_length)
        # matching data's shape — data=[observed.sharpe_ratio] has length 1, so this is
        # (n_resamples, 1) in practice, not a plain int. Confirmed by direct testing, not
        # assumed from the docstring's `rvs(size=(m, n))` example.
        n = size[0] if isinstance(size, tuple) else size
        sharpes = np.empty(n)
        next_seed = seed
        for i in range(n):
            for attempt in range(_MAX_RETRIES_PER_DRAW):
                control_cls = make_random_entry_strategy(rule, observed.num_trades, seed=next_seed)
                next_seed += 1
                control = run_backtest(price_data, control_cls, ticker=ticker, **kwargs)
                if control.num_trades > 0:
                    break
            else:
                raise ValueError(
                    f"random-entry control produced 0 trades in {_MAX_RETRIES_PER_DRAW} "
                    f"consecutive attempts (observed_num_trades={observed.num_trades} may be "
                    "too low relative to the data length)"
                )
            sharpes[i] = control.sharpe_ratio
        return sharpes.reshape(size)

    mc = monte_carlo_test(
        data=[observed.sharpe_ratio],
        rvs=rvs,
        statistic=lambda x: x,
        n_resamples=n_resamples,
        alternative="greater",
    )
    null_dist = mc.null_distribution.reshape(-1)

    return SignificanceResult(
        observed_sharpe=observed.sharpe_ratio,
        observed_num_trades=observed.num_trades,
        p_value=mc.pvalue.item(),
        n_resamples=n_resamples,
        null_mean_sharpe=float(np.mean(null_dist)),
        null_std_sharpe=float(np.std(null_dist)),
    )
