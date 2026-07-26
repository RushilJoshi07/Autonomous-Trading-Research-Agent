from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from data_pipeline.db.models import PriceBar, TickerMetadata


def get_last_cached_date(session: Session, ticker: str) -> date | None:
    return session.execute(
        select(func.max(PriceBar.date)).where(PriceBar.ticker == ticker)
    ).scalar()


def upsert_price_bars(session: Session, ticker: str, df: pd.DataFrame) -> int:
    """Insert or update price bars. On conflict, updates adj_* and fetched_at only — raw_* is never overwritten.

    Returns the number of rows processed.
    """
    if df.empty:
        return 0

    rows = []
    for idx, row in df.iterrows():
        rows.append({
            "ticker": ticker,
            "date": idx if isinstance(idx, date) else idx.date(),
            "raw_open":   Decimal(str(row["raw_open"])),
            "raw_high":   Decimal(str(row["raw_high"])),
            "raw_low":    Decimal(str(row["raw_low"])),
            "raw_close":  Decimal(str(row["raw_close"])),
            "raw_volume": int(row["raw_volume"]),
            "adj_open":   Decimal(str(row["adj_open"])),
            "adj_high":   Decimal(str(row["adj_high"])),
            "adj_low":    Decimal(str(row["adj_low"])),
            "adj_close":  Decimal(str(row["adj_close"])),
            "adj_volume": int(row["adj_volume"]),
            "fetched_at": row["fetched_at"],
        })

    stmt = pg_insert(PriceBar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date"],
        set_={
            "adj_open":   stmt.excluded.adj_open,
            "adj_high":   stmt.excluded.adj_high,
            "adj_low":    stmt.excluded.adj_low,
            "adj_close":  stmt.excluded.adj_close,
            "adj_volume": stmt.excluded.adj_volume,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    session.execute(stmt)
    return len(rows)


def upsert_metadata(session: Session, ticker: str, metadata: dict) -> None:
    stmt = pg_insert(TickerMetadata).values(
        ticker=ticker,
        sector=metadata.get("sector"),
        industry=metadata.get("industry"),
        listing_status=metadata.get("listing_status"),
        updated_at=datetime.now(tz=timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_={
            "sector":         stmt.excluded.sector,
            "industry":       stmt.excluded.industry,
            "listing_status": stmt.excluded.listing_status,
            "updated_at":     stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)
