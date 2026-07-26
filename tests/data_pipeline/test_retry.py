"""Tests for the retry/backoff behaviour in the yfinance fetcher.

We test at two levels:
  - _history (the retried private function): verify it retries N times before succeeding
  - fetch_prices (the public function): verify it raises FetchError after exhaustion

Tenacity captures the sleep function by reference at decoration time, so patching
time.sleep after the fact is too late. We patch the sleep attribute on the Retrying
instance directly (accessible as _history.retry.sleep) to make retries instant.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data_pipeline.fetch.client import FetchError
from data_pipeline.fetch.prices import _history, fetch_prices

_START = date(2024, 1, 2)
_END = date(2024, 1, 3)

# A minimal DataFrame that satisfies fetch_prices's merge logic
_GOOD_DF = pd.DataFrame(
    {
        "Open":   [100.0],
        "High":   [105.0],
        "Low":    [99.0],
        "Close":  [102.0],
        "Volume": [1_000_000],
    },
    index=pd.DatetimeIndex(["2024-01-02"], tz="UTC"),
)
_GOOD_DF.index.name = "Date"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Prevent tenacity from actually sleeping between retry attempts.

    Tenacity stores its sleep callable on the Retrying instance at decoration time
    (not looked up through time.sleep on each call), so we patch the instance directly.
    """
    from data_pipeline.fetch.prices import _history
    monkeypatch.setattr(_history.retry, "sleep", lambda _: None)


def test_retries_on_transient_failure():
    """_history retries and succeeds on the third attempt.

    We test _history directly (not through fetch_prices) so a single
    yfinance call is involved — fetch_prices makes two _history calls
    (raw + adjusted), which would double the count.
    """
    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = [
        Exception("attempt 1 fails"),
        Exception("attempt 2 fails"),
        _GOOD_DF,  # third attempt succeeds
    ]

    with patch("data_pipeline.fetch.prices.yf.Ticker", return_value=mock_ticker):
        result = _history("AAPL", _START, _END, auto_adjust=False)

    assert mock_ticker.history.call_count == 3, (
        f"Expected 3 history calls (2 failures + 1 success), "
        f"got {mock_ticker.history.call_count}"
    )
    assert result is _GOOD_DF


def test_fetch_error_on_exhaustion():
    """fetch_prices raises FetchError after all 3 attempts fail.

    The ingest runner catches FetchError per ticker. This test confirms the
    exception type is correct — a bare Exception would propagate uncaught and
    crash the whole run rather than just failing the one ticker.
    """
    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = Exception("persistent yfinance error")

    with patch("data_pipeline.fetch.prices.yf.Ticker", return_value=mock_ticker):
        with pytest.raises(FetchError):
            fetch_prices("AAPL", start=_START, end=_END)

    # 3 calls for raw (all fail → FetchError raised immediately, adj never called)
    assert mock_ticker.history.call_count == 3
