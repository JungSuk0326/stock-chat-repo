"""One-shot script to sync the KR instrument master into the database.

Usage:
    cd backend && uv run python -m app.scripts.sync_instruments

Behavior:
    - Calls KrMarketAdapter.fetch_instruments() (FDR bulk listing)
    - UPSERTs into `instruments` keyed on (exchange, symbol)
    - Existing rows get their market/name/isin/updated_at refreshed
    - Future: this logic will be wrapped by the discovery worker on a daily cadence
"""

import asyncio

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models import Instrument
from app.services.market.kr import KrMarketAdapter

log = structlog.get_logger()


async def sync_kr_instruments() -> int:
    adapter = KrMarketAdapter()
    instruments = await adapter.fetch_instruments()

    if not instruments:
        log.warning("sync.instruments.empty")
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

    log.info("sync.instruments.done", count=len(instruments))
    return len(instruments)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
    count = asyncio.run(sync_kr_instruments())
    print(f"Synced {count} instruments.")


if __name__ == "__main__":
    main()
