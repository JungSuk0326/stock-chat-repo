"""Worker process entry point.

Started by docker-compose as the `worker` service:
    command: uv run python -m app.workers.runner

Hosts APScheduler with all worker jobs. Currently:
  - price_poller: 2s polling of KR realtime prices → Redis cache/pub + 1m bars

Future jobs (placeholder list — see CLAUDE.md):
  disclosure_watcher, news_collector, board_crawler, discovery_runner,
  alert_runner, dart_corp_sync.
"""

from __future__ import annotations

import asyncio
import signal

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis_client import redis_client
from app.services.market.kr import KrMarketAdapter
from app.workers.price_poller import PricePoller

log = structlog.get_logger()

# Phase 1: poll one symbol. Watchlist iteration comes later.
WATCHLIST: tuple[tuple[str, str], ...] = (("KR", "005930"),)  # 삼성전자

POLL_INTERVAL_SECONDS = 2


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)

    log.info(
        "worker.startup",
        environment=settings.ENVIRONMENT,
        enabled_markets=settings.enabled_markets,
        watchlist=[f"{ex}:{sym}" for ex, sym in WATCHLIST],
        poll_interval_s=POLL_INTERVAL_SECONDS,
    )

    # One adapter (= one http client) shared across pollers in this process.
    kr_adapter = KrMarketAdapter()

    pollers = [
        PricePoller(
            exchange=ex,
            symbol=sym,
            adapter=kr_adapter,
            redis=redis_client,
        )
        for ex, sym in WATCHLIST
    ]

    scheduler = AsyncIOScheduler(timezone="UTC")
    for poller in pollers:
        scheduler.add_job(
            poller.tick,
            "interval",
            seconds=POLL_INTERVAL_SECONDS,
            id=f"price_poller_{poller.exchange}_{poller.symbol}",
            coalesce=True,        # if previous tick still running, skip rather than queue
            max_instances=1,
            misfire_grace_time=POLL_INTERVAL_SECONDS,
        )
    scheduler.start()

    # Wait for SIGINT/SIGTERM so docker compose stop is graceful.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("worker.shutdown")

    scheduler.shutdown(wait=False)
    await kr_adapter.aclose()
    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
