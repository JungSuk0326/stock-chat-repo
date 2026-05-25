"""Manually sync the KR instrument master.

Thin wrapper over app.services.instruments.sync_kr_instruments (the worker
runs the same service on a daily 06:00 KST cron).

Usage:
    uv run python -m app.scripts.sync_instruments
"""

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.instruments import sync_kr_instruments
from app.services.market.kr import KrMarketAdapter


async def _run() -> int:
    adapter = KrMarketAdapter()
    try:
        return await sync_kr_instruments(adapter)
    finally:
        await adapter.aclose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
    count = asyncio.run(_run())
    print(f"Synced {count} instruments.")


if __name__ == "__main__":
    main()
