from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_pipeline.db.models import CorporateActionLog, PriceBar
from data_pipeline.fetch.corporate_actions import fetch_corporate_actions
from data_pipeline.fetch.prices import fetch_prices
from data_pipeline.ingest.upsert import upsert_price_bars


def _known_actions(session: Session, ticker: str) -> set[tuple]:
    rows = session.execute(
        select(CorporateActionLog.action_type, CorporateActionLog.action_date)
        .where(CorporateActionLog.ticker == ticker)
    ).all()
    return {(r.action_type, r.action_date) for r in rows}


def handle_corporate_actions(session: Session, ticker: str) -> int:
    """Detect new corporate actions for a ticker and re-fetch adjusted prices if any are found.

    Raw prices are never touched. Only adj_* columns are updated.
    Returns the number of new actions detected and logged.
    Raises FetchError (from fetch_corporate_actions or fetch_prices) if yfinance fails.
    """
    all_actions = fetch_corporate_actions(ticker)
    known = _known_actions(session, ticker)

    new_actions = [
        a for a in all_actions
        if (a["action_type"], a["action_date"]) not in known
    ]

    if not new_actions:
        return 0

    earliest = session.execute(
        select(PriceBar.date)
        .where(PriceBar.ticker == ticker)
        .order_by(PriceBar.date)
        .limit(1)
    ).scalar()

    if earliest:
        df = fetch_prices(ticker, start=earliest)
        upsert_price_bars(session, ticker, df)

    for action in new_actions:
        session.add(CorporateActionLog(
            ticker=ticker,
            action_type=action["action_type"],
            action_date=action["action_date"],
            value=action["value"],
            detected_at=datetime.now(tz=timezone.utc),
        ))

    return len(new_actions)
