"""Fundamentals cache-first fetch.

The screener engine asks for fundamentals on `instrument_id`s that passed
the technical pre-filter. Most calls hit the cache because most symbols
in any given screener run were touched within the last 24h. yfinance
calls happen only for stale/missing rows.

Concurrency: yfinance is sync per-ticker (~1 sec each via asyncio.to_thread).
For N stale symbols, we run them with a small concurrency cap so Yahoo
doesn't see a burst — `MAX_CONCURRENT_FETCH` keeps it polite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.models import FundamentalsSnapshot, Instrument
from app.services.fundamentals.base import FundamentalsAdapter, FundamentalsData

log = structlog.get_logger()

CACHE_TTL_HOURS = 24
MAX_CONCURRENT_FETCH = 5


async def _upsert(data: FundamentalsData, instrument_id: int) -> None:
    now = datetime.now(timezone.utc)
    row = {
        "instrument_id": instrument_id,
        "fetched_at": now,
        "per": data.per,
        "forward_per": data.forward_per,
        "pbr": data.pbr,
        "dividend_yield": data.dividend_yield,
        "market_cap": data.market_cap,
        "beta": data.beta,
        "sector": data.sector,
        "industry": data.industry,
    }
    async with SessionLocal() as session:
        stmt = pg_insert(FundamentalsSnapshot).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id"],
            set_={k: stmt.excluded[k] for k in row if k != "instrument_id"} | {
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
        await session.commit()


async def _read_cached(
    instrument_ids: Iterable[int],
) -> dict[int, FundamentalsSnapshot]:
    """Bulk-fetch existing rows. Returns {instrument_id: row}."""
    ids = list(instrument_ids)
    if not ids:
        return {}
    async with SessionLocal() as session:
        stmt = select(FundamentalsSnapshot).where(
            FundamentalsSnapshot.instrument_id.in_(ids)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return {r.instrument_id: r for r in rows}


def _is_stale(row: FundamentalsSnapshot, now: datetime) -> bool:
    return (now - row.fetched_at) > timedelta(hours=CACHE_TTL_HOURS)


async def get_fundamentals_for_instruments(
    adapter: FundamentalsAdapter,
    instruments: list[Instrument],
) -> dict[int, FundamentalsSnapshot]:
    """Return up-to-date FundamentalsSnapshot rows for each instrument.

    Cache lookup first; for any missing/stale entry, hit yfinance, upsert,
    and re-read. The returned dict is keyed by instrument_id and only
    includes symbols where the fetch succeeded — callers handle the
    "no fundamentals available" case (e.g., symbol Yahoo doesn't know).
    """
    if not instruments:
        return {}

    now = datetime.now(timezone.utc)
    cached = await _read_cached(i.id for i in instruments)

    stale_or_missing: list[Instrument] = [
        inst for inst in instruments
        if inst.id not in cached or _is_stale(cached[inst.id], now)
    ]

    if stale_or_missing:
        await _refresh_batch(adapter, stale_or_missing)
        # Re-read everything (rows changed)
        cached = await _read_cached(i.id for i in instruments)

    return cached


async def _refresh_batch(
    adapter: FundamentalsAdapter, instruments: list[Instrument]
) -> int:
    """Concurrent yfinance fetches, capped to MAX_CONCURRENT_FETCH."""
    sem = asyncio.Semaphore(MAX_CONCURRENT_FETCH)
    updated = 0

    async def _one(inst: Instrument) -> None:
        nonlocal updated
        async with sem:
            data = await adapter.fetch(inst.symbol)
            if data is None:
                log.info(
                    "fundamentals.upstream_no_data",
                    exchange=inst.exchange,
                    symbol=inst.symbol,
                )
                return
            await _upsert(data, inst.id)
            updated += 1

    log.info("fundamentals.refresh.started", count=len(instruments))
    await asyncio.gather(*(_one(i) for i in instruments), return_exceptions=False)
    log.info("fundamentals.refresh.done", attempted=len(instruments), updated=updated)
    return updated


async def refresh_watchlist(adapter: FundamentalsAdapter) -> int:
    """Force-refresh fundamentals for every watchlist symbol. Called by
    the daily worker — keeps the cache warm for screeners + UI uses."""
    from app.models import WatchlistEntry
    from sqlalchemy.orm import joinedload

    async with SessionLocal() as session:
        stmt = (
            select(WatchlistEntry)
            .options(joinedload(WatchlistEntry.instrument))
            .join(Instrument)
            .where(Instrument.exchange == adapter.market_code)
        )
        entries = (await session.execute(stmt)).scalars().all()
    instruments = [e.instrument for e in entries]
    return await _refresh_batch(adapter, instruments)
