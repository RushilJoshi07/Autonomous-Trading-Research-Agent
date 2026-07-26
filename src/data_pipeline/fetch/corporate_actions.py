from datetime import date
from decimal import Decimal

import yfinance as yf

from data_pipeline.fetch.client import FetchError, retry_on_failure


@retry_on_failure
def _fetch_actions(ticker: str):
    return yf.Ticker(ticker).actions


def fetch_corporate_actions(ticker: str) -> list[dict]:
    """Return all splits and dividends for a ticker as a flat list of action records.

    Each record: {"action_type": "split"|"dividend", "action_date": date, "value": Decimal}
    Zero-value rows (yfinance pads absent actions with 0) are excluded.
    Raises FetchError if yfinance fails after retries.
    """
    try:
        actions_df = _fetch_actions(ticker)
    except Exception as exc:
        raise FetchError(f"{ticker}: {exc}") from exc

    if actions_df is None or actions_df.empty:
        return []

    records = []
    for ts, row in actions_df.iterrows():
        action_date = ts.normalize().tz_localize(None).date()
        if row.get("Stock Splits", 0) != 0:
            records.append({
                "action_type": "split",
                "action_date": action_date,
                "value": Decimal(str(row["Stock Splits"])),
            })
        if row.get("Dividends", 0) != 0:
            records.append({
                "action_type": "dividend",
                "action_date": action_date,
                "value": Decimal(str(row["Dividends"])),
            })

    return records
