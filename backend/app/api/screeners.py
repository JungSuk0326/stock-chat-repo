"""Screener REST — CRUD + manual run.

Companion to /candidates which surfaces the engine's output. Manual run
is useful for "I just tweaked the criteria, show me results now" without
waiting for the 17:30 KST daily cron.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.discovery import (
    ScreenerCreateRequest,
    ScreenerListResponse,
    ScreenerRunResponse,
    ScreenerSummary,
    ScreenerUpdateRequest,
)
from app.services.discovery import run_screener
from app.services.discovery_crud import (
    candidate_count_by_screener,
    create_screener,
    delete_screener,
    get_screener,
    list_screeners,
    update_screener,
)
from app.services.fundamentals.base import FundamentalsAdapter

router = APIRouter(prefix="/screeners", tags=["screeners"])


def _fundamentals_adapter(request: Request) -> FundamentalsAdapter:
    """Reuse the lifespan-managed adapter — sharing httpx clients across
    the manual-run endpoint and the daily cron keeps connection pooling
    behavior consistent."""
    # The backend doesn't currently keep a fundamentals adapter in
    # app.state (worker does). Lazily instantiate per request — yfinance
    # has no auth and the cache layer hides most calls.
    from app.services.fundamentals.kr import YFinanceKrAdapter
    adapter = getattr(request.app.state, "fundamentals_adapter", None)
    if adapter is None:
        adapter = YFinanceKrAdapter()
        request.app.state.fundamentals_adapter = adapter
    return adapter


async def _to_summary(db: AsyncSession, screener, user_id: int) -> ScreenerSummary:
    count = await candidate_count_by_screener(
        db, user_id=user_id, screener_id=screener.id
    )
    return ScreenerSummary(
        id=screener.id,
        name=screener.name,
        description=screener.description,
        universe=screener.universe or {},
        criteria=list(screener.criteria or []),
        enabled=screener.enabled,
        last_run_at=screener.last_run_at,
        created_at=screener.created_at,
        updated_at=screener.updated_at,
        candidate_count=count,
    )


@router.get("", response_model=ScreenerListResponse)
async def list_user_screeners(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ScreenerListResponse:
    rows = await list_screeners(db, user_id=user_id)
    items = [await _to_summary(db, r, user_id) for r in rows]
    return ScreenerListResponse(count=len(items), items=items)


@router.post("", response_model=ScreenerSummary, status_code=201)
async def create_user_screener(
    body: ScreenerCreateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ScreenerSummary:
    row = await create_screener(
        db,
        user_id=user_id,
        name=body.name,
        description=body.description,
        universe=body.universe,
        criteria=body.criteria,
        enabled=body.enabled,
    )
    return await _to_summary(db, row, user_id)


@router.get("/{screener_id}", response_model=ScreenerSummary)
async def get_user_screener(
    screener_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ScreenerSummary:
    try:
        row = await get_screener(db, screener_id=screener_id, user_id=user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Screener not found")
    return await _to_summary(db, row, user_id)


@router.patch("/{screener_id}", response_model=ScreenerSummary)
async def patch_user_screener(
    screener_id: int,
    body: ScreenerUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ScreenerSummary:
    try:
        row = await update_screener(
            db,
            screener_id=screener_id,
            user_id=user_id,
            name=body.name,
            description=body.description,
            universe=body.universe,
            criteria=body.criteria,
            enabled=body.enabled,
        )
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Screener not found")
    return await _to_summary(db, row, user_id)


@router.delete("/{screener_id}", status_code=204)
async def delete_user_screener(
    screener_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> None:
    try:
        await delete_screener(db, screener_id=screener_id, user_id=user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Screener not found")


@router.post("/{screener_id}/run", response_model=ScreenerRunResponse)
async def run_user_screener(
    screener_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ScreenerRunResponse:
    """Manual run — same code path as the daily cron, just on-demand."""
    try:
        # Ownership check via get_screener; run_screener itself trusts the
        # screener id (engine is user-agnostic by design — runs whatever
        # is enabled). So we verify ownership before invoking.
        await get_screener(db, screener_id=screener_id, user_id=user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Screener not found")

    adapter = _fundamentals_adapter(request)
    new_count = await run_screener(screener_id, adapter)
    return ScreenerRunResponse(screener_id=screener_id, new_candidates=new_count)
