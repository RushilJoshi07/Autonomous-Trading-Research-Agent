import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from data_pipeline.db.models import IngestionRun, IngestionRunTicker
from data_pipeline.db.session import SessionFactory
from data_pipeline.fetch.client import FetchError
from data_pipeline.fetch.metadata import fetch_metadata
from data_pipeline.fetch.prices import fetch_prices
from data_pipeline.ingest.corporate_actions import handle_corporate_actions
from data_pipeline.ingest.upsert import get_last_cached_date, upsert_metadata, upsert_price_bars

logger = logging.getLogger(__name__)

_DEFAULT_START = date(2010, 1, 1)


def _create_run() -> str:
    run_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(IngestionRun(
            id=run_id,
            started_at=datetime.now(tz=timezone.utc),
            status="in_progress",
        ))
        session.commit()
    return run_id


def _finish_run(run_id: str) -> None:
    with SessionFactory() as session:
        ticker_rows = session.execute(
            select(IngestionRunTicker).where(IngestionRunTicker.run_id == run_id)
        ).scalars().all()
        successes = sum(1 for t in ticker_rows if t.status == "success")
        failures  = sum(1 for t in ticker_rows if t.status == "failed")
        if failures == 0:
            status = "success"
        elif successes == 0:
            status = "failed"
        else:
            status = "partial_success"
        run = session.get(IngestionRun, run_id)
        run.finished_at = datetime.now(tz=timezone.utc)
        run.status = status
        session.commit()


def _log_ticker_error(run_id: str, ticker: str, error: Exception) -> None:
    with SessionFactory() as session:
        session.add(IngestionRunTicker(
            run_id=run_id,
            ticker=ticker,
            status="failed",
            rows_written=0,
            error=str(error),
        ))
        session.commit()


def ingest_daily(tickers: list[str]) -> str:
    """Fetch and cache any bars not yet in the database for each ticker.

    Each ticker runs in its own transaction. A failure on one ticker does not
    affect the others. Returns the run_id.
    """
    run_id = _create_run()
    for ticker in tickers:
        try:
            with SessionFactory() as session:
                last = get_last_cached_date(session, ticker)
                start = (last + timedelta(days=1)) if last else _DEFAULT_START
                df = fetch_prices(ticker, start=start)
                meta = fetch_metadata(ticker)
                rows = upsert_price_bars(session, ticker, df)
                upsert_metadata(session, ticker, meta)
                session.add(IngestionRunTicker(
                    run_id=run_id, ticker=ticker, status="success", rows_written=rows,
                ))
                session.commit()
        except FetchError as exc:
            logger.warning("Fetch failed for %s: %s", ticker, exc)
            _log_ticker_error(run_id, ticker, exc)
    _finish_run(run_id)
    return run_id


def full_refetch(tickers: list[str]) -> str:
    """Re-fetch the full price history for each ticker, updating adj_* on existing rows.

    Returns the run_id.
    """
    run_id = _create_run()
    for ticker in tickers:
        try:
            with SessionFactory() as session:
                df = fetch_prices(ticker, start=_DEFAULT_START)
                meta = fetch_metadata(ticker)
                rows = upsert_price_bars(session, ticker, df)
                upsert_metadata(session, ticker, meta)
                session.add(IngestionRunTicker(
                    run_id=run_id, ticker=ticker, status="success", rows_written=rows,
                ))
                session.commit()
        except FetchError as exc:
            logger.warning("Full refetch failed for %s: %s", ticker, exc)
            _log_ticker_error(run_id, ticker, exc)
    _finish_run(run_id)
    return run_id


def check_corporate_actions(tickers: list[str]) -> None:
    """Detect new splits/dividends and re-fetch adjusted prices where needed."""
    for ticker in tickers:
        try:
            with SessionFactory() as session:
                n = handle_corporate_actions(session, ticker)
                session.commit()
                if n:
                    logger.info("Logged %d new corporate action(s) for %s", n, ticker)
        except FetchError as exc:
            logger.warning("Corporate action check failed for %s: %s", ticker, exc)
