"""User model — minimum-viable scaffolding.

The app is still single-user (CLAUDE.md). We add the `users` table now so
new tables (`chat_sessions`, `chat_messages`, future `alerts`/`screeners`)
can carry `user_id` from day one. A bootstrap row "owner" (id=1) is
inserted by the same migration that creates the table.

Authentication itself remains AUTH_PASSWORD-based. When real multi-user
auth lands (R3 → Cloudflare Access, or own JWT), only the dependency
that resolves the current user changes — schema stays.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

OWNER_USER_ID: int = 1


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
