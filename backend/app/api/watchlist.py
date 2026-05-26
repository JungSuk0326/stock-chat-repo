from datetime import date, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Instrument, WatchlistEntry
from app.schemas.watchlist import AddToWatchlistRequest, WatchlistItem
from app.services.market.kr import KrMarketAdapter
from app.services.prices import sync_eod_prices

log = structlog.get_logger()

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

BACKFILL_DAYS = 365


def _kr_adapter(request: Request) -> KrMarketAdapter:
    """Lifespan-managed shared adapter (one httpx client per backend process)."""
    return request.app.state.kr_adapter


@router.get("", response_model=list[WatchlistItem])
async def list_watchlist(db: AsyncSession = Depends(get_db)) -> list[WatchlistItem]:
    """All watchlist rows, ordered by user-defined position then recency."""
    stmt = (
        select(WatchlistEntry)
        .order_by(WatchlistEntry.position.asc(), WatchlistEntry.added_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [WatchlistItem.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=WatchlistItem,
    status_code=status.HTTP_201_CREATED,
)
async def add_to_watchlist(
    body: AddToWatchlistRequest,
    db: AsyncSession = Depends(get_db),
    kr_adapter: KrMarketAdapter = Depends(_kr_adapter),
) -> WatchlistItem:
    exchange = body.exchange.upper().strip()
    symbol = body.symbol.strip()

    instrument = (
        await db.execute(
            select(Instrument).where(
                Instrument.exchange == exchange,
                Instrument.symbol == symbol,
            )
        )
    ).scalar_one_or_none()
    if instrument is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument not found: {exchange}:{symbol}",
        )

    entry = WatchlistEntry(instrument_id=instrument.id, position=body.position)
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        # UNIQUE(instrument_id) 위반 → 이미 등록됨
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Already in watchlist: {exchange}:{symbol}",
        )
    await db.refresh(entry, attribute_names=["instrument"])

    # Synchronous backfill so the user lands on a populated chart immediately.
    # ~200-500ms extra to the POST response, but eliminates the 30s wait for the
    # worker's reconcile loop. UPSERT-idempotent — the worker will harmlessly
    # re-run the same backfill on its next reconcile.
    if exchange == "KR":
        try:
            end = date.today()
            start = end - timedelta(days=BACKFILL_DAYS)
            await sync_eod_prices(kr_adapter, exchange, symbol, start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "watchlist.immediate_backfill_failed",
                exchange=exchange,
                symbol=symbol,
                error=str(exc),
            )

    return WatchlistItem.model_validate(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_watchlist(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    entry = (
        await db.execute(select(WatchlistEntry).where(WatchlistEntry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Watchlist entry {entry_id} not found")
    await db.delete(entry)
    await db.commit()
