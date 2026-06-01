"""ORM model for collected news headlines.

Storage policy: headline + url + publisher + timestamp. **Body is never
stored** — copyright/약관 concerns + LLM context only needs headlines.

`source_id` is the publisher's stable article id (Naver returns a
12-digit office+article composite). `UNIQUE(source, source_id)` keeps
INSERT...ON CONFLICT DO NOTHING idempotent across re-polls.
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


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_news_items_source_id"),
        # Most queries: "latest N news for this instrument"
        Index(
            "ix_news_items_instrument_published",
            "instrument_id",
            "published_at",
        ),
    )
