# Stage 4: replaces Stage 1's hand-picked static list (git history for the
# original 17-ticker version). The actual universe is now whatever's been
# ingested into TickerMetadata — it grows automatically as tickers are added
# (via the screener's own one-time broadening, or later manual ingestion),
# rather than requiring this file to be hand-edited every time.
#
# Still NOT point-in-time — reflects today's ingested set only. Delisted and
# bankrupt names remain excluded, so results are subject to survivorship bias.
# See docs/architecture.md section 6 for the full discussion.

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import TickerMetadata


def all_tickers(session: Session) -> list[str]:
    """Every ticker with metadata currently ingested, sorted."""
    rows = session.execute(select(TickerMetadata.ticker)).scalars().all()
    return sorted(rows)
