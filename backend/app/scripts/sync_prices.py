"""One-shot script to sync EOD prices for a single KR instrument.

Usage:
    # Default: 삼성전자 1 year back from today
    uv run python -m app.scripts.sync_prices

    # Explicit
    uv run python -m app.scripts.sync_prices --symbol 005930 --start 2024-01-01 --end 2024-12-31

Behavior:
    - Looks up Instrument by (exchange, symbol). Aborts if not found.
    - Calls KrMarketAdapter.fetch_eod_prices() (pykrx)
    - UPSERTs into `prices` keyed on (instrument_id, interval, time)
    - interval = "1d" for now
"""

import argparse
import asyncio
from datetime import date, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models import Instrument, Price
from app.services.market.kr import KrMarketAdapter

log = structlog.get_logger()


async def sync_eod_prices(
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Instrument).where(
                Instrument.exchange == "KR",
                Instrument.symbol == symbol,
            )
        )
        instrument = result.scalar_one_or_none()
        if instrument is None:
            log.error("instrument.not_found", exchange="KR", symbol=symbol)
            return 0

        adapter = KrMarketAdapter()
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
        "prices.synced",
        symbol=symbol,
        count=len(prices),
        start=str(start),
        end=str(end),
    )
    return len(prices)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync EOD prices for a KR instrument.")
    today = date.today()
    p.add_argument(
        "--symbol",
        default="005930",
        help="6-digit KR symbol (default: 005930 삼성전자)",
    )
    p.add_argument(
        "--start",
        type=lambda s: date.fromisoformat(s),
        default=today - timedelta(days=365),
        help="Start date ISO format (default: 1 year ago)",
    )
    p.add_argument(
        "--end",
        type=lambda s: date.fromisoformat(s),
        default=today,
        help="End date ISO format (default: today)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
    count = asyncio.run(sync_eod_prices(args.symbol, args.start, args.end))
    print(f"Synced {count} daily bars for KR:{args.symbol} ({args.start} → {args.end}).")


if __name__ == "__main__":
    main()
