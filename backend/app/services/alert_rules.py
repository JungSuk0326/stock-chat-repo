"""Alert rule CRUD service.

Thin wrapper over the ORM so the API and (future) LLM tools share one
canonical path for create/list/delete. When Top5 lands, the tool-use
functions call these same helpers — no duplication of validation logic.
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertRule, Instrument
from app.services.alerts import CONDITION_TYPES

log = structlog.get_logger()


async def resolve_instrument(
    db: AsyncSession, exchange: str, symbol: str
) -> Instrument | None:
    """Look up the canonical instrument by (exchange, symbol)."""
    return (
        await db.execute(
            select(Instrument).where(
                Instrument.exchange == exchange.upper().strip(),
                Instrument.symbol == symbol.strip(),
            )
        )
    ).scalar_one_or_none()


async def list_rules(
    db: AsyncSession,
    *,
    user_id: int,
    instrument_id: int | None = None,
) -> list[AlertRule]:
    """All rules for `user_id`, optionally restricted to one instrument."""
    stmt = (
        select(AlertRule)
        .where(AlertRule.user_id == user_id)
        .order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
    )
    if instrument_id is not None:
        stmt = stmt.where(AlertRule.instrument_id == instrument_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_rule(
    db: AsyncSession, *, rule_id: int, user_id: int
) -> AlertRule:
    """Fetch a single rule, scoped to user_id. Raises NoResultFound on
    missing OR ownership mismatch — caller maps to 404."""
    stmt = (
        select(AlertRule)
        .where(AlertRule.id == rule_id)
        .where(AlertRule.user_id == user_id)
    )
    rule = (await db.execute(stmt)).scalar_one_or_none()
    if rule is None:
        raise NoResultFound(f"alert rule {rule_id} not found")
    return rule


async def create_rule(
    db: AsyncSession,
    *,
    user_id: int,
    instrument_id: int,
    condition_type: str,
    threshold: Decimal,
    name: str | None = None,
    cooldown_minutes: int = 60,
    market_hours_only: bool = False,
) -> AlertRule:
    """Validate + INSERT. Raises ValueError on bad condition_type."""
    ct = condition_type.strip()
    if ct not in CONDITION_TYPES:
        raise ValueError(
            f"Unknown condition_type: {ct!r} (allowed: {sorted(CONDITION_TYPES)})"
        )
    if cooldown_minutes < 1:
        raise ValueError("cooldown_minutes must be >= 1")

    rule = AlertRule(
        user_id=user_id,
        instrument_id=instrument_id,
        name=(name or None),
        condition_type=ct,
        threshold=threshold,
        enabled=True,
        cooldown_minutes=cooldown_minutes,
        market_hours_only=market_hours_only,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    log.info(
        "alert_rule.created",
        rule_id=rule.id,
        user_id=user_id,
        instrument_id=instrument_id,
        condition=ct,
    )
    return rule


async def update_rule_enabled(
    db: AsyncSession, *, rule_id: int, user_id: int, enabled: bool
) -> AlertRule:
    rule = await get_rule(db, rule_id=rule_id, user_id=user_id)
    rule.enabled = enabled
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_rule(
    db: AsyncSession, *, rule_id: int, user_id: int
) -> None:
    rule = await get_rule(db, rule_id=rule_id, user_id=user_id)
    await db.delete(rule)
    await db.commit()
