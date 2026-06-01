"""News persistence service.

Mirrors disclosures.py — adapter + UPSERT-idempotent INSERT, plus
watchlist-wide sweep used by the 5-minute poller. Body is never persisted
(adapter doesn't even carry it — see app/services/news/kr.py).
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app.core.db import SessionLocal
from app.models import Instrument, NewsItem, WatchlistEntry
from app.services.news.base import NewsAdapter, NewsItemData

log = structlog.get_logger()


async def _resolve_instrument_id(*, exchange: str, symbol: str) -> int | None:
    async with SessionLocal() as session:
        stmt = (
            select(Instrument.id)
            .where(Instrument.exchange == exchange)
            .where(Instrument.symbol == symbol)
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def _upsert_news(
    instrument_id: int, items: Iterable[NewsItemData]
) -> int:
    """ON CONFLICT(source, source_id) DO NOTHING. Returns new-row count."""
    rows = [
        {
            "instrument_id": instrument_id,
            "source": it.source,
            "source_id": it.source_id,
            "title": it.title,
            "published_at": it.published_at,
            "url": it.url,
            "publisher": it.publisher,
        }
        for it in items
    ]
    if not rows:
        return 0
    async with SessionLocal() as session:
        stmt = pg_insert(NewsItem).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["source", "source_id"]
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def sync_news_for_symbol(
    adapter: NewsAdapter,
    *,
    exchange: str,
    symbol: str,
    limit: int = 50,
) -> int:
    """Fetch latest `limit` news items for one symbol and UPSERT.

    Used for both the per-poll sweep AND the new-watchlist backfill. The
    adapter doesn't expose a date range — Naver's endpoint returns the
    freshest items, which is what we want in both cases.
    """
    instrument_id = await _resolve_instrument_id(exchange=exchange, symbol=symbol)
    if instrument_id is None:
        log.warning("news.sync.no_instrument", exchange=exchange, symbol=symbol)
        return 0
    items = await adapter.fetch_news(symbol, limit=limit)
    inserted = await _upsert_news(instrument_id, items)
    if inserted:
        log.info(
            "news.sync.new",
            exchange=exchange,
            symbol=symbol,
            new=inserted,
            seen=len(items),
        )
    return inserted


async def sync_news_watchlist(adapter: NewsAdapter, *, limit: int = 30) -> int:
    """Per-symbol sweep across the KR watchlist. Returns total new rows.

    `limit` is per symbol — Naver returns newest first, so 30 well covers
    a 5-minute window for even noisy stocks.
    """
    if adapter.market_code != "KR":
        log.warning("news.watchlist.unsupported", market=adapter.market_code)
        return 0

    async with SessionLocal() as session:
        stmt = (
            select(WatchlistEntry)
            .options(joinedload(WatchlistEntry.instrument))
            .join(Instrument)
            .where(Instrument.exchange == "KR")
        )
        entries = (await session.execute(stmt)).scalars().all()

    total = 0
    for entry in entries:
        inst = entry.instrument
        total += await sync_news_for_symbol(
            adapter,
            exchange=inst.exchange,
            symbol=inst.symbol,
            limit=limit,
        )
    return total
