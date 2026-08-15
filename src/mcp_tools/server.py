from datetime import date

from mcp.server import MCPServer

from backtester.data_loader import load_price_data
from data_pipeline.db.session import SessionFactory
from mcp_tools.schemas import PriceBarOut

mcp = MCPServer("agentic-finance-platform")


@mcp.tool()
def get_price_data(ticker: str, start: date | None = None, end: date | None = None) -> list[PriceBarOut]:
    """Daily OHLCV bars for a ticker from the cached database (splits/dividends adjusted)."""
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
    return [
        PriceBarOut(
            date=row.Index.date(),
            open=row.Open,
            high=row.High,
            low=row.Low,
            close=row.Close,
            volume=int(row.Volume),
        )
        for row in df.itertuples()
    ]


if __name__ == "__main__":
    mcp.run()
