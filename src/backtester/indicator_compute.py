"""Standalone indicator computation, independent of a backtesting.py Strategy.

rule_strategy.py already computes indicator series, but only wired through
backtesting.py's self.I() inside a running Strategy. This is the same
computation (same registry, same normalize_params, same select_output_column)
invoked directly against a plain price DataFrame, for callers that just want
one indicator's values -- the MCP indicators tool being the first one.
"""

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from .data_loader import load_price_data
from .indicators import normalize_params, select_output_column
from .registry import ALL_INDICATORS
from .schema import IndicatorTerm

_FIELD_TO_COLUMN = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}


def compute_indicator(
    ticker: str,
    name: str,
    params: dict[str, float],
    session: Session,
    start: date | None = None,
    end: date | None = None,
) -> pd.Series:
    """Compute one named indicator's full series for a ticker.

    Raises ValueError if name is unknown/unverified or params are out of bounds
    (via IndicatorTerm's own validation) or if the underlying pandas-ta call fails.
    """
    IndicatorTerm(name=name, params=params)  # validation only; result discarded

    spec = ALL_INDICATORS[name]
    df = load_price_data(ticker, session, start=start, end=end)
    price_args = [df[_FIELD_TO_COLUMN[field]] for field in spec.inputs]
    result = spec.fn(*price_args, **normalize_params(params))
    if result is None:
        raise ValueError(f"{name}: pandas-ta returned None — check inputs (e.g. a required DatetimeIndex)")
    return select_output_column(result, spec.column_prefix)
