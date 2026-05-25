"""EOD price persistence service.

Single source of truth for "fetch from adapter → UPSERT into `prices`".
Used by:
  - app/scripts/sync_prices.py   (manual CLI)
  - app/workers/runner.py        (auto-backfill on new watchlist symbol)

The worker will eventually run this on a daily 16:00 KST cron too.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app.core.db import SessionLocal
from app.models import Instrument, Price, WatchlistEntry
from app.services.market.base import MarketAdapter

log = structlog.get_logger()


async def sync_eod_prices(
    adapter: MarketAdapter,
    exchange: str,
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> int:
    """Fetch EOD bars from `adapter` and UPSERT into `prices`.

    Returns the number of bars synced (0 if instrument missing or empty range).
    Idempotent: ON CONFLICT(instrument_id, interval, time) DO UPDATE.
    """
    async with SessionLocal() as session:
        instrument = (
            await session.execute(
                select(Instrument).where(
                    Instrument.exchange == exchange,
                    Instrument.symbol == symbol,
                )
            )
        ).scalar_one_or_none()
        if instrument is None:
            log.error(
                "sync_eod.instrument_not_found",
                exchange=exchange,
                symbol=symbol,
            )
            return 0

        prices = await adapter.fetch_eod_prices(symbol, start, end)
        if not prices:
            return 0

        rows = [
            {
                "instrument_id": instrument.id,
                "interval": interval,
                "time": p.time,
                "open": p.open,
                "high": p.high,
                "low": p.low,
                "close": p.close,
                "volume": p.volume,
            }
            for p in prices
        ]

        stmt = pg_insert(Price).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "interval", "time"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        await session.execute(stmt)
        await session.commit()

    log.info(
        "sync_eod.done",
        exchange=exchange,
        symbol=symbol,
        count=len(prices),
        start=str(start),
        end=str(end),
    )
    return len(prices)


async def sync_eod_watchlist(
    adapter: MarketAdapter,
    days: int = 7,
) -> dict[str, int]:
    """Run EOD sync for every KR symbol in the watchlist.

    `days` = lookback window. Default 7 covers most worker-downtime gaps and
    is cheap on pykrx. Idempotent UPSERT, so repeating is harmless.

    Returns {canonical_id: bars_synced} so callers can log per-symbol results.
    """
    end = date.today()
    start = end - timedelta(days=days)

    async with SessionLocal() as session:
        entries = (
            await session.execute(
                select(WatchlistEntry).options(
                    joinedload(WatchlistEntry.instrument)
                )
            )
        ).scalars().all()

    log.info(
        "eod_sync.start",
        instruments=len(entries),
        start=str(start),
        end=str(end),
        days=days,
    )

    result: dict[str, int] = {}
    for entry in entries:
        inst = entry.instrument
        if inst.exchange != "KR":
            # Phase 1: only KR adapter wired in. Skip other markets quietly.
            continue
        canonical = f"{inst.exchange}:{inst.symbol}"
        try:
            count = await sync_eod_prices(adapter, inst.exchange, inst.symbol, start, end)
            result[canonical] = count
        except Exception as exc:  # noqa: BLE001 — keep going on other symbols
            log.warning(
                "eod_sync.symbol_failed",
                canonical=canonical,
                error=str(exc),
            )
            result[canonical] = 0

    total = sum(result.values())
    log.info("eod_sync.done", instruments=len(result), total_bars=total)
    return result
