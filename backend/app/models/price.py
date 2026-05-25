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
    """OHLCV bar for an instrument at a given interval and time.

    Stored as a TimescaleDB hypertable partitioned on `time` (chunk interval = 7 days).
    Multiple intervals (1d/1h/1m/...) coexist via the `interval` column.

    All times in UTC (project rule). The bar's `time` is its open timestamp.
    """

    __tablename__ = "prices"

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    interval: Mapped[str] = mapped_column(String(8), nullable=False)  # "1d", "1h", "1m"
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        PrimaryKeyConstraint(
            "instrument_id", "interval", "time", name="pk_prices"
        ),
    )
