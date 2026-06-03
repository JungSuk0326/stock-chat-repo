"""Investor flow persistence service.

Mirrors the news/disclosures pattern: adapter fetch → ON CONFLICT DO NOTHING
INSERT keyed on UNIQUE(instrument_id, trade_date). Re-poll is idempotent,
so the same daily cron tick can safely run on a half-populated table.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app.core.db import SessionLocal
from app.models import Instrument, InvestorFlow, WatchlistEntry
from app.services.investor_flow.base import InvestorFlowAdapter, InvestorFlowData

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


async def _upsert_flows(
    instrument_id: int, items: Iterable[InvestorFlowData], source: str
) -> int:
    rows = [
        {
            "instrument_id": instrument_id,
            "trade_date": it.trade_date,
            "foreign_net_volume": it.foreign_net_volume,
            "foreign_hold_ratio": it.foreign_hold_ratio,
            "institutional_net_volume": it.institutional_net_volume,
            "individual_net_volume": it.individual_net_volume,
            "close_price": it.close_price,
            "source": source,
        }
        for it in items
    ]
    if not rows:
        return 0
    async with SessionLocal() as session:
        stmt = pg_insert(InvestorFlow).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["instrument_id", "trade_date"]
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def sync_investor_flow_for_symbol(
    adapter: InvestorFlowAdapter,
    *,
    exchange: str,
    symbol: str,
    days: int = 30,
) -> int:
    """Fetch newest `days` of investor flow and UPSERT. Used by both the
    daily worker (days=30 ought to cover any short downtime) and the new
    watchlist backfill (days=60 to seed a chart-friendly window).
    """
    instrument_id = await _resolve_instrument_id(exchange=exchange, symbol=symbol)
    if instrument_id is None:
        log.warning(
            "investor_flow.sync.no_instrument",
            exchange=exchange,
            symbol=symbol,
        )
        return 0
    items = await adapter.fetch_recent(symbol, days=days)
    inserted = await _upsert_flows(instrument_id, items, source=adapter.source)
    if inserted:
        log.info(
            "investor_flow.sync.new",
            exchange=exchange,
            symbol=symbol,
            new=inserted,
            seen=len(items),
        )
    return inserted


async def sync_investor_flow_watchlist(
    adapter: InvestorFlowAdapter, *, days: int = 30
) -> int:
    """Sweep watchlist for one market. Returns total newly-inserted rows."""
    if adapter.market_code != "KR":
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
        total += await sync_investor_flow_for_symbol(
            adapter,
            exchange=inst.exchange,
            symbol=inst.symbol,
            days=days,
        )
    return total
