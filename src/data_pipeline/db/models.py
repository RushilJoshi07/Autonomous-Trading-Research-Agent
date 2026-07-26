import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PriceBar(Base):
    __tablename__ = "price_bars"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)

    raw_open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    raw_volume: Mapped[int] = mapped_column(BigInteger)

    adj_open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    adj_volume: Mapped[int] = mapped_column(BigInteger)

    fetched_at: Mapped[datetime] = mapped_column()


class TickerMetadata(Base):
    __tablename__ = "ticker_metadata"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    listing_status: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column()


class CorporateActionLog(Base):
    __tablename__ = "corporate_actions_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    action_type: Mapped[str] = mapped_column(String(16))  # "split" or "dividend"
    action_date: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    detected_at: Mapped[datetime] = mapped_column()


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    started_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # success / partial_success / failed

    tickers: Mapped[list["IngestionRunTicker"]] = relationship(back_populates="run")


class IngestionRunTicker(Base):
    __tablename__ = "ingestion_run_tickers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # success / failed
    rows_written: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["IngestionRun"] = relationship(back_populates="tickers")
