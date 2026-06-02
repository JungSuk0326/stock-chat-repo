"""Fundamentals snapshot — cache layer over the yfinance adapter.

One row per instrument (UNIQUE on instrument_id), updated in-place when a
fresh fetch happens. `fetched_at` drives the TTL: callers that need
"fresh enough" data check the age before deciding to re-fetch.

Why no history: screener evaluation only needs the latest. Fundamental
trend over time can be added later as a separate table if/when we want
"PER history" charts.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FundamentalsSnapshot(Base):
    __tablename__ = "fundamentals_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # All metric columns are nullable — yfinance returns partial data
    # depending on the symbol's listing type / freshness / Yahoo's mood.
    per: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    forward_per: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    pbr: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    dividend_yield: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )  # percent
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    beta: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", name="uq_fundamentals_snapshot_instrument"
        ),
        # Screeners often filter "fresher than 24h" — index on fetched_at
        # lets us prune stale rows without a sequential scan.
        Index("ix_fundamentals_snapshot_fetched_at", "fetched_at"),
    )
