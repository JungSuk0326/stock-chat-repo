from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Instrument(Base):
    """A tradable instrument identified by (exchange, symbol).

    Internal canonical identifier is the string `{exchange}:{symbol}`
    (e.g. "KR:005930", "US:AAPL"). External API identifiers
    (e.g. yfinance's "005930.KS") are mapped inside per-market adapters.
    """

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Identity (natural key)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)

    # Metadata
    country: Mapped[str] = mapped_column(String(2), nullable=False)  # ISO 3166-1 alpha-2
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)  # e.g. KOSPI, KOSDAQ, NYSE
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True, unique=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Audit (UTC per project timezone rule)
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
        UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),
    )

    @property
    def canonical_id(self) -> str:
        return f"{self.exchange}:{self.symbol}"
