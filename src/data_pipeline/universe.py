# Stage 1 universe: a small hand-picked list used during development.
# This is NOT point-in-time — it reflects today's listings only.
# Delisted and bankrupt names are excluded, which means results are subject to
# survivorship bias. See docs/architecture.md section 6 for the full discussion.
# The screener tool (Stage 4) will replace this list.

TICKERS: list[str] = [
    # Large-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    # Financials
    "JPM", "BAC", "GS",
    # Energy
    "XOM", "CVX",
    # Healthcare
    "JNJ", "UNH",
    # Consumer
    "WMT", "PG",
    # Small/mid cap (to exercise edge cases in fetcher)
    "CROX", "BROS",
    # ETF (tests nullable sector/industry in metadata)
    "SPY",
]
