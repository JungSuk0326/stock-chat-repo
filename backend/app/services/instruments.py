"""Instrument master persistence service.

Single source of truth for "fetch from adapter → UPSERT into `instruments`".
Used by:
  - app/scripts/sync_instruments.py   (manual CLI)
  - app/workers/runner.py             (daily 06:00 KST cron)
"""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.models import Instrument
from app.services.market.base import MarketAdapter

log = structlog.get_logger()


async def sync_kr_instruments(adapter: MarketAdapter) -> int:
    """Fetch the entire KR instrument master from the adapter and UPSERT.

    Idempotent ON CONFLICT(exchange, symbol). Returns the number of rows
    fetched from the source (not the number changed in DB).
    """
    instruments = await adapter.fetch_instruments()
    if not instruments:
        log.warning("sync_instruments.empty")
        return 0

    rows = [i.model_dump() for i in instruments]

    async with SessionLocal() as session:
        stmt = pg_insert(Instrument).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["exchange", "symbol"],
            set_={
                "market": stmt.excluded.market,
                "name": stmt.excluded.name,
                "isin": stmt.excluded.isin,
                "country": stmt.excluded.country,
                "currency": stmt.excluded.currency,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
        await session.commit()

    log.info("sync_instruments.done", count=len(instruments))
    return len(instruments)
