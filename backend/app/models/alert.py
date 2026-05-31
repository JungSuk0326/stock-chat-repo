"""Alert rule + event ORM models.

Two tables on purpose:

- `alert_rules` — the persistent rule definition. The evaluator reads it
  every minute. `last_triggered_at` + `cooldown_minutes` prevent the same
  rule from firing repeatedly during a sustained breach.

- `alert_events` — one row per actual delivery attempt. Lets you answer
  "왜 알림 안 왔지?" by reading the events table (delivery_status, channel,
  triggered_value). Cleared on rule delete via CASCADE.

Condition shape kept deliberately narrow for Phase 1 (Top4):
  - price_above / price_below — `threshold` compared against current price
  - pct_change_above / pct_change_below — `threshold` compared against
    today's percentage change vs prev close

Adding new types later means: (1) extend `CONDITION_TYPES` set in the
runner, (2) handle the type in the evaluator. Schema unchanged.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

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

    name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # See module docstring for the supported values
    condition_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Once fired, the rule is silenced for this many minutes. Prevents a
    # rule like "price > 320,000" from firing every minute the price holds
    # above that level.
    cooldown_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    # Phase 1 default = False (notify any time, useful for "drop below"
    # alerts overnight). Operator can enable per-rule.
    market_hours_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    last_triggered_at: Mapped[datetime | None] = mapped_column(
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
        # Evaluator scans enabled rules per user. Filtering by enabled in
        # the index keeps disabled rules out of the hot path.
        Index(
            "ix_alert_rules_user_enabled",
            "user_id",
            "enabled",
            "instrument_id",
        ),
    )


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    rule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
    )

    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The actual value that crossed the threshold — kept so the message
    # body can reference "current price 320,500" and so post-mortems can
    # see exactly what triggered the rule.
    triggered_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # "telegram", "log"
    delivery_status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "sent" | "failed" | "skipped"
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_alert_events_rule_fired",
            "rule_id",
            "fired_at",
        ),
    )
