"""EOD price persistence service.

Single source of truth for "fetch from adapter → UPSERT into `prices`".
Used by:
  - app/scripts/sync_prices.py   (manual CLI)
  - app/workers/runner.py        (auto-backfill on new watchlist symbol)

The worker will eventually run this on a daily 16:00 KST cron too.
"""

from __future__ import annotations

from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.models import Instrument, Price
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
