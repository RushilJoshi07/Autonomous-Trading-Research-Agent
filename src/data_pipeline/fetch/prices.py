from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from data_pipeline.fetch.client import FetchError, retry_on_failure


@retry_on_failure
def _history(ticker: str, start: date, end: date | None, auto_adjust: bool) -> pd.DataFrame:
    return yf.Ticker(ticker).history(
        start=start,
        end=end,
        auto_adjust=auto_adjust,
        actions=False,
    )


def fetch_prices(ticker: str, start: date, end: date | None = None) -> pd.DataFrame:
    """Return a DataFrame with raw and adjusted OHLCV columns for the given date range.

    Columns: date, raw_open, raw_high, raw_low, raw_close, raw_volume,
             adj_open, adj_high, adj_low, adj_close, adj_volume.
    Raises FetchError if yfinance fails after retries.
    """
    try:
        raw = _history(ticker, start, end, auto_adjust=False)
        adj = _history(ticker, start, end, auto_adjust=True)
    except Exception as exc:
        raise FetchError(f"{ticker}: {exc}") from exc

    if raw.empty or adj.empty:
        return pd.DataFrame()

    raw.index = raw.index.normalize().tz_localize(None)
    adj.index = adj.index.normalize().tz_localize(None)

    merged = pd.DataFrame({
        "raw_open":   raw["Open"],
        "raw_high":   raw["High"],
        "raw_low":    raw["Low"],
        "raw_close":  raw["Close"],
        "raw_volume": raw["Volume"],
        "adj_open":   adj["Open"],
        "adj_high":   adj["High"],
        "adj_low":    adj["Low"],
        "adj_close":  adj["Close"],
        "adj_volume": adj["Volume"],
    }).dropna().sort_index()

    merged.index.name = "date"
    merged["fetched_at"] = datetime.now(tz=timezone.utc)
    return merged
