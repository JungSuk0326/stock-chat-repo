import asyncio
from datetime import date, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Instrument, WatchlistEntry
from app.schemas.watchlist import AddToWatchlistRequest, WatchlistItem
from app.services.disclosure.kr import DartAdapter
from app.services.disclosures import sync_disclosures_for_symbol
from app.services.market.kr import KrMarketAdapter
from app.services.news.kr import NaverNewsAdapter
from app.services.news_sync import sync_news_for_symbol
from app.services.prices import sync_eod_prices

log = structlog.get_logger()

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

BACKFILL_DAYS = 365
# DART 공시 backfill: 가입 시점부터 6개월치
DISCLOSURE_BACKFILL_DAYS = 180
# 네이버 뉴스 backfill: newest first 한 페이지
NEWS_BACKFILL_LIMIT = 50


def _kr_adapter(request: Request) -> KrMarketAdapter:
    """Lifespan-managed shared adapter (one httpx client per backend process)."""
    return request.app.state.kr_adapter


def _dart_adapter(request: Request) -> DartAdapter:
    return request.app.state.dart_adapter


def _news_adapter(request: Request) -> NaverNewsAdapter:
    return request.app.state.news_adapter


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
    dart_adapter: DartAdapter = Depends(_dart_adapter),
    news_adapter: NaverNewsAdapter = Depends(_news_adapter),
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

    # Synchronous backfill for prices so the user lands on a populated chart
    # immediately. ~200-500ms extra to the POST response, but eliminates the
    # 30s wait for the worker's reconcile loop. UPSERT-idempotent — the
    # worker will harmlessly re-run the same backfill on its next reconcile.
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

        # Disclosure + news backfill fire-and-forget — don't block the POST
        # response (DART can be slow on the corp_code lookup, Naver is fast
        # but no point making the user wait). MarketFeedPanel polls every
        # 60s so panels populate within that window even if the user
        # switches symbols immediately.
        async def _backfill_disclosures() -> None:
            try:
                end_kst = date.today()
                start_kst = end_kst - timedelta(days=DISCLOSURE_BACKFILL_DAYS)
                await sync_disclosures_for_symbol(
                    dart_adapter,
                    exchange=exchange,
                    symbol=symbol,
                    start=start_kst,
                    end=end_kst,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "watchlist.disclosure_backfill_failed",
                    symbol=symbol,
                    error=str(exc),
                )

        async def _backfill_news() -> None:
            try:
                await sync_news_for_symbol(
                    news_adapter,
                    exchange=exchange,
                    symbol=symbol,
                    limit=NEWS_BACKFILL_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "watchlist.news_backfill_failed",
                    symbol=symbol,
                    error=str(exc),
                )

        asyncio.create_task(_backfill_disclosures())
        asyncio.create_task(_backfill_news())

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
