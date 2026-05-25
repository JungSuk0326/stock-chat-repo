from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.instrument import Instrument


class WatchlistEntry(Base):
    """A user's interest in an instrument.

    Single-user app: there's only one watchlist (no `user_id` column).
    Multi-user would add user_id + change the UNIQUE to (user_id, instrument_id).
    """

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one entry per instrument
    )

    # User-defined ordering. 0 = top, ties broken by added_at desc.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    instrument: Mapped[Instrument] = relationship(lazy="joined")
