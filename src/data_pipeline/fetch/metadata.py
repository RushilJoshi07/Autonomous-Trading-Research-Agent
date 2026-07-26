import yfinance as yf

from data_pipeline.fetch.client import FetchError, retry_on_failure


@retry_on_failure
def _fetch_info(ticker: str) -> dict:
    return yf.Ticker(ticker).info


def fetch_metadata(ticker: str) -> dict:
    """Return sector, industry, and listing_status for a ticker.

    Values are None when yfinance does not provide them (common for ETFs).
    Raises FetchError if yfinance fails after retries.
    """
    try:
        info = _fetch_info(ticker)
    except Exception as exc:
        raise FetchError(f"{ticker}: {exc}") from exc

    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "listing_status": "active" if info.get("regularMarketPrice") else "unknown",
    }
