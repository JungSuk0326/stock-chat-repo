"""One-shot CLI to sync EOD prices for a single KR instrument.

Thin wrapper over app.services.prices.sync_eod_prices (the worker uses the
same service for auto-backfill).

Usage:
    uv run python -m app.scripts.sync_prices
    uv run python -m app.scripts.sync_prices --symbol 005930 --start 2024-01-01 --end 2024-12-31
"""

import argparse
import asyncio
from datetime import date, timedelta

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.market.kr import KrMarketAdapter
from app.services.prices import sync_eod_prices


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync EOD prices for a KR instrument.")
    today = date.today()
    p.add_argument("--symbol", default="005930", help="6-digit KR symbol")
    p.add_argument(
        "--start",
        type=date.fromisoformat,
        default=today - timedelta(days=365),
        help="Start date ISO (default: 1 year ago)",
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        default=today,
        help="End date ISO (default: today)",
    )
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    adapter = KrMarketAdapter()
    try:
        return await sync_eod_prices(adapter, "KR", args.symbol, args.start, args.end)
    finally:
        await adapter.aclose()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
    count = asyncio.run(_run(args))
    print(f"Synced {count} daily bars for KR:{args.symbol} ({args.start} → {args.end}).")


if __name__ == "__main__":
    main()
