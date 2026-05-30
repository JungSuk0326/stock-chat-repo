"""ORM models for regulator-issued disclosures.

Two tables:

- `corp_codes` — regulator's corp-id ↔ (exchange, symbol) mapping. DART's
  `corp_code` (8-digit) joined to a 6-digit `stock_code`; later, SEC's
  CIK joined to a ticker. Refreshed daily (R11).

- `disclosures` — one row per filing. `filed_at` is the official
  publication timestamp in UTC (DART gives date-only, pinned to 00:00 KST).
  `source_id` is the regulator's stable filing id (DART rcept_no, SEC
  accession number) and is the basis for idempotent UPSERT.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CorpCode(Base):
    __tablename__ = "corp_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False)  # "dart" | "sec"
    source_corp_id: Mapped[str] = mapped_column(String(32), nullable=False)

    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
        UniqueConstraint("source", "source_corp_id", name="uq_corp_codes_source_id"),
        Index("ix_corp_codes_exchange_symbol", "exchange", "symbol"),
    )


class Disclosure(Base):
    __tablename__ = "disclosures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    filed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    report_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_disclosures_source_id"),
        # Most queries: "latest N disclosures for an instrument". This index
        # turns that into a single range scan.
        Index(
            "ix_disclosures_instrument_filed_at",
            "instrument_id",
            "filed_at",
        ),
    )
