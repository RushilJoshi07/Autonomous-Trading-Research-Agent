from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from data_pipeline.db.models import PriceBar


def load_price_data(
    ticker: str,
    session: Session,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Return a DataFrame in backtesting.py format for the given ticker.

    Reads adj_* columns from price_bars and renames them to the capitalized
    names backtesting.py expects: Open, High, Low, Close, Volume.
    Index is a DatetimeIndex (required by backtesting.py).

    Raises ValueError if no rows are found for the ticker/range.
    """
    stmt = select(PriceBar).where(PriceBar.ticker == ticker)
    if start:
        stmt = stmt.where(PriceBar.date >= start)
    if end:
        stmt = stmt.where(PriceBar.date <= end)
    stmt = stmt.order_by(PriceBar.date)

    rows = session.execute(stmt).scalars().all()
    if not rows:
        raise ValueError(f"No price data found for {ticker} ({start} – {end})")

    df = pd.DataFrame(
        {
            "Open":   [float(r.adj_open)  for r in rows],
            "High":   [float(r.adj_high)  for r in rows],
            "Low":    [float(r.adj_low)   for r in rows],
            "Close":  [float(r.adj_close) for r in rows],
            "Volume": [int(r.adj_volume)  for r in rows],
        },
        index=pd.DatetimeIndex([pd.Timestamp(r.date) for r in rows]),
    )
    df.index.name = "Date"
    return df
