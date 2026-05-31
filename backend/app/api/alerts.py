"""Alert rule REST endpoints.

Phase 1 — manual CRUD. Top5 will add LLM tool-use that calls into the
same `app.services.alert_rules` functions, so this router stays as the
single source of truth for the operations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.models import AlertEvent, Instrument
from app.schemas.alert import (
    AlertEventRecord,
    AlertRuleCreateRequest,
    AlertRuleListResponse,
    AlertRuleSummary,
    AlertRuleUpdateRequest,
)
from app.services.alert_rules import (
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    resolve_instrument,
    update_rule_enabled,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])


async def _to_summary(db: AsyncSession, rule) -> AlertRuleSummary:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.id == rule.instrument_id)
        )
    ).scalar_one()
    return AlertRuleSummary(
        id=rule.id,
        instrument_id=rule.instrument_id,
        instrument=f"{inst.exchange}:{inst.symbol}",
        name=rule.name,
        condition_type=rule.condition_type,
        threshold=rule.threshold,
        enabled=rule.enabled,
        cooldown_minutes=rule.cooldown_minutes,
        market_hours_only=rule.market_hours_only,
        last_triggered_at=rule.last_triggered_at,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.get("", response_model=AlertRuleListResponse)
async def list_alert_rules(
    exchange: str | None = Query(default=None, max_length=8),
    symbol: str | None = Query(default=None, max_length=32),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> AlertRuleListResponse:
    """List the caller's alert rules, newest-first. Filter by instrument
    if both `exchange` and `symbol` are passed."""
    instrument_id: int | None = None
    if exchange or symbol:
        if not (exchange and symbol):
            raise HTTPException(
                status_code=400,
                detail="exchange and symbol must be provided together",
            )
        inst = await resolve_instrument(db, exchange, symbol)
        if inst is None:
            raise HTTPException(
                status_code=404,
                detail=f"Instrument not found: {exchange}:{symbol}",
            )
        instrument_id = inst.id

    rules = await list_rules(db, user_id=user_id, instrument_id=instrument_id)
    items = [await _to_summary(db, r) for r in rules]
    return AlertRuleListResponse(count=len(items), items=items)


@router.post("", response_model=AlertRuleSummary, status_code=201)
async def create_alert_rule(
    body: AlertRuleCreateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> AlertRuleSummary:
    inst = await resolve_instrument(db, body.exchange, body.symbol)
    if inst is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument not found: {body.exchange}:{body.symbol}",
        )
    try:
        rule = await create_rule(
            db,
            user_id=user_id,
            instrument_id=inst.id,
            condition_type=body.condition_type,
            threshold=body.threshold,
            name=body.name,
            cooldown_minutes=body.cooldown_minutes,
            market_hours_only=body.market_hours_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _to_summary(db, rule)


@router.patch("/{rule_id}", response_model=AlertRuleSummary)
async def patch_alert_rule(
    rule_id: int,
    body: AlertRuleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> AlertRuleSummary:
    try:
        rule = await update_rule_enabled(
            db, rule_id=rule_id, user_id=user_id, enabled=body.enabled
        )
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return await _to_summary(db, rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> None:
    try:
        await delete_rule(db, rule_id=rule_id, user_id=user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Alert rule not found")


@router.get("/{rule_id}/events", response_model=list[AlertEventRecord])
async def list_rule_events(
    rule_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> list[AlertEventRecord]:
    """Recent fire history. Owner-scoped via `get_rule` (404 if not owned)."""
    try:
        await get_rule(db, rule_id=rule_id, user_id=user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    events = (
        (
            await db.execute(
                select(AlertEvent)
                .where(AlertEvent.rule_id == rule_id)
                .order_by(AlertEvent.fired_at.desc(), AlertEvent.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [AlertEventRecord.model_validate(e) for e in events]
