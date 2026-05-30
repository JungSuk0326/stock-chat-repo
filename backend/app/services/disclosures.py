"""Disclosure persistence service.

Three high-level operations:

  - sync_corp_codes(adapter)              — daily 05:30 KST cron (R11)
  - sync_disclosures_for_symbol(...)      — backfill on new watchlist add
  - sync_disclosures_watchlist(...)       — per-minute polling

All UPSERTs are idempotent (ON CONFLICT DO NOTHING for disclosures, DO UPDATE
for corp_codes), so re-runs after worker downtime or restart are free.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app.core.db import SessionLocal
from app.models import CorpCode, Disclosure, Instrument, WatchlistEntry
from app.services.disclosure.base import DisclosureAdapter, DisclosureData

log = structlog.get_logger()


async def sync_corp_codes(adapter: DisclosureAdapter) -> int:
    """Fetch the regulator's corp-id master and UPSERT.

    For DART this is the full CORPCODE.xml (~80k entries, of which ~3k are
    listed and pass the adapter filter). Idempotent.
    """
    entries = await adapter.fetch_corp_codes()
    if not entries:
        log.warning("disclosures.corp_codes.empty", source=adapter.source)
        return 0

    rows = [e.model_dump() for e in entries]
    async with SessionLocal() as session:
        stmt = pg_insert(CorpCode).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_corp_id"],
            set_={
                "exchange": stmt.excluded.exchange,
                "symbol": stmt.excluded.symbol,
                "name": stmt.excluded.name,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
        await session.commit()

    log.info("disclosures.corp_codes.done", source=adapter.source, count=len(rows))
    return len(rows)


async def _resolve_corp_id(
    *, source: str, exchange: str, symbol: str
) -> str | None:
    """Look up the regulator's corp-id for (exchange, symbol).

    Returns None if the symbol has no mapping yet — caller should skip.
    Happens for newly-listed firms before the daily corp_code sync runs.
    """
    async with SessionLocal() as session:
        stmt = (
            select(CorpCode.source_corp_id)
            .where(CorpCode.source == source)
            .where(CorpCode.exchange == exchange)
            .where(CorpCode.symbol == symbol)
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def _resolve_instrument_id(*, exchange: str, symbol: str) -> int | None:
    async with SessionLocal() as session:
        stmt = (
            select(Instrument.id)
            .where(Instrument.exchange == exchange)
            .where(Instrument.symbol == symbol)
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def _upsert_disclosures(
    instrument_id: int, items: Iterable[DisclosureData]
) -> int:
    """INSERT ... ON CONFLICT DO NOTHING on (source, source_id). Returns the
    number of *new* rows inserted (Postgres reports rowcount accordingly)."""
    rows = [
        {
            "instrument_id": instrument_id,
            "source": d.source,
            "source_id": d.source_id,
            "title": d.title,
            "filed_at": d.filed_at,
            "report_type": d.report_type,
            "submitter": d.submitter,
            "raw_url": d.raw_url,
        }
        for d in items
    ]
    if not rows:
        return 0

    async with SessionLocal() as session:
        stmt = pg_insert(Disclosure).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["source", "source_id"]
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def sync_disclosures_for_symbol(
    adapter: DisclosureAdapter,
    *,
    exchange: str,
    symbol: str,
    start: date,
    end: date,
) -> int:
    """Fetch [start, end] disclosures for one symbol and UPSERT.

    Used for backfill on new watchlist additions (e.g. 6 months) and the
    per-minute poller (start=end=today).

    Returns the count of *newly inserted* rows. Skips silently if the symbol
    isn't mapped in corp_codes yet, or if the regulator has no record.
    """
    source = adapter.source
    corp_id = await _resolve_corp_id(source=source, exchange=exchange, symbol=symbol)
    if corp_id is None:
        log.info(
            "disclosures.sync.no_corp_mapping",
            source=source,
            exchange=exchange,
            symbol=symbol,
        )
        return 0

    instrument_id = await _resolve_instrument_id(exchange=exchange, symbol=symbol)
    if instrument_id is None:
        log.warning(
            "disclosures.sync.no_instrument",
            exchange=exchange,
            symbol=symbol,
        )
        return 0

    items = await adapter.fetch_recent_disclosures(corp_id, start, end)
    inserted = await _upsert_disclosures(instrument_id, items)
    if inserted:
        log.info(
            "disclosures.sync.new",
            source=source,
            exchange=exchange,
            symbol=symbol,
            new=inserted,
            seen=len(items),
        )
    return inserted


async def sync_disclosures_watchlist(
    adapter: DisclosureAdapter,
    *,
    start: date,
    end: date,
) -> int:
    """Walk every watchlist entry of `adapter`'s market and sync [start, end].

    Returns total newly-inserted rows across all symbols.
    """
    market_to_exchange = {"dart": "KR"}
    exchange = market_to_exchange.get(adapter.source)
    if exchange is None:
        log.warning("disclosures.watchlist.unknown_source", source=adapter.source)
        return 0

    async with SessionLocal() as session:
        stmt = (
            select(WatchlistEntry)
            .options(joinedload(WatchlistEntry.instrument))
            .join(Instrument)
            .where(Instrument.exchange == exchange)
        )
        entries = (await session.execute(stmt)).scalars().all()

    total = 0
    for entry in entries:
        inst = entry.instrument
        total += await sync_disclosures_for_symbol(
            adapter,
            exchange=inst.exchange,
            symbol=inst.symbol,
            start=start,
            end=end,
        )
    return total
