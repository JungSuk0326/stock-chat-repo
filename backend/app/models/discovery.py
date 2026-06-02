"""Discovery domain — screener definitions + candidate lifecycle.

`screeners`:
  Saved filter rules. `criteria` is a JSONB list of conditions evaluated
  with AND logic. `universe` is a JSONB filter against `instruments`
  (e.g., `{"market": "KOSPI"}`). Both are flexible to let us add new
  condition types without schema migration — the runner switches on
  `criteria[i]["type"]`.

`candidates`:
  Symbols a screener (or LLM in Top8) flagged. Status machine:
    new       → user hasn't reviewed
    snoozed   → user said "remind me later" (snoozed_until is when)
    promoted  → moved to watchlist
    dismissed → user said "not interested"; future re-flags ignored
  UNIQUE(user_id, instrument_id, source) so the same screener's
  re-runs idempotently keep one row per (user, symbol, source).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Screener(Base):
    __tablename__ = "screeners"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSONB so condition types can evolve without migrations.
    # Shape: {"market": "KOSPI"} | {"market": "KOSDAQ"} | {} (all KR)
    universe: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    # Shape: [{"type": "technical:rsi_below", "value": 30}, ...]
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
        Index("ix_screeners_user_enabled", "user_id", "enabled"),
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )

    # "screener:<id>" / "llm" / "manual" — keeps source attribution
    # without forcing a separate FK table; cheap and flexible.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # new | snoozed | promoted | dismissed
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new"
    )

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
            "user_id", "instrument_id", "source",
            name="uq_candidates_user_instrument_source",
        ),
        Index(
            "ix_candidates_user_status_discovered",
            "user_id", "status", "discovered_at",
        ),
    )
