"""Manually trigger the daily EOD sync for every KR symbol in watchlist.

The worker runs this on a 16:00 KST cron automatically. This script is for:
  - immediate verification after deploy
  - catch-up when the worker has been down for a while
  - debugging a specific lookback window

Usage:
    uv run python -m app.scripts.sync_eod_watchlist
    uv run python -m app.scripts.sync_eod_watchlist --days 30
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.market.kr import KrMarketAdapter
from app.services.prices import sync_eod_watchlist


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync recent EOD bars for every watchlist symbol.")
    p.add_argument("--days", type=int, default=7, help="Lookback days (default 7)")
    return p.parse_args()


async def _run(days: int) -> dict[str, int]:
    adapter = KrMarketAdapter()
    try:
        return await sync_eod_watchlist(adapter, days=days)
    finally:
        await adapter.aclose()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
    result = asyncio.run(_run(args.days))
    total = sum(result.values())
    print(f"\nEOD sync done: {len(result)} instruments, {total} total bars over {args.days} days.")
    for canonical, count in sorted(result.items()):
        print(f"  {canonical}: {count} bars")


if __name__ == "__main__":
    main()
