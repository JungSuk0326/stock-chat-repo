from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Price(Base):
    """OHLCV bar for an instrument at a given interval and time, **per venue**.

    Stored as a TimescaleDB hypertable partitioned on `time` (chunk interval = 7 days).
    Multiple intervals (1d/1h/1m/...) coexist via the `interval` column.

    `venue` separates the two trading venues for KR instruments — KRX (정규
    거래소) and NXT (넥스트레이드 ATS). Same symbol can trade on both
    simultaneously during 09:00-15:20 KST (concurrent main session) and only
    on NXT during 08:00-08:50 / 15:30-20:00 KST (NXT extended sessions).
    For non-KR markets `venue` is just the exchange's own code (NYSE, NASDAQ).

    All times in UTC (project rule). The bar's `time` is its open timestamp.
    """

    __tablename__ = "prices"

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    interval: Mapped[str] = mapped_column(String(8), nullable=False)  # "1d", "1h", "1m"
    venue: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="KRX"
    )  # KRX / NXT (KR) — NYSE/NASDAQ etc. later
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        # Compound PK includes `venue` so KRX and NXT bars at the same minute
        # coexist. `time` stays in the PK (TimescaleDB hypertable rule).
        PrimaryKeyConstraint(
            "instrument_id", "interval", "venue", "time", name="pk_prices"
        ),
    )
