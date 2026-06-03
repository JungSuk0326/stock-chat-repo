"""Daily investor-type net buy/sell per instrument.

One row per (instrument, trade_date). UNIQUE constraint keeps re-polls
idempotent — Naver re-emits the same ~60 trading days every call, we
just INSERT ... ON CONFLICT DO NOTHING.

Net volumes are signed BigInteger (대형주는 일일 ±수천만 주). Hold ratio
is percent NUMERIC(6,2) — values like 48.11.
"""

from datetime import date as date_type, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
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


class InvestorFlow(Base):
    __tablename__ = "investor_flows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    trade_date: Mapped[date_type] = mapped_column(Date, nullable=False)

    foreign_net_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    foreign_hold_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    institutional_net_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    individual_net_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)

    close_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="naver")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "trade_date",
            name="uq_investor_flows_instrument_date",
        ),
        # "이 종목의 최근 N일 수급" — 단일 범위 스캔
        Index(
            "ix_investor_flows_instrument_date",
            "instrument_id", "trade_date",
        ),
    )
