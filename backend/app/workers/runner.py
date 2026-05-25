"""Worker process entry point.

Started by docker-compose as the `worker` service:
    command: uv run python -m app.workers.runner

Hosts APScheduler with all worker jobs:
  - watchlist_sync (every 30s) — reconcile polling jobs with DB watchlist
  - price_poller_{exchange}_{symbol} (every 2s) — one per watchlist entry,
    added/removed dynamically by watchlist_sync

Future jobs (placeholder list — see CLAUDE.md):
  disclosure_watcher, news_collector, board_crawler, discovery_runner,
  alert_runner, dart_corp_sync, eod_sync.
"""

from __future__ import annotations

import asyncio
import signal

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.core.redis_client import redis_client
from app.models import WatchlistEntry
from app.services.market.kr import KrMarketAdapter
from app.workers.price_poller import PricePoller

log = structlog.get_logger()

POLL_INTERVAL_SECONDS = 2
WATCHLIST_SYNC_INTERVAL_SECONDS = 30


def _poller_job_id(exchange: str, symbol: str) -> str:
    return f"price_poller_{exchange}_{symbol}"


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)

    log.info(
        "worker.startup",
        environment=settings.ENVIRONMENT,
        enabled_markets=settings.enabled_markets,
        poll_interval_s=POLL_INTERVAL_SECONDS,
        watchlist_sync_interval_s=WATCHLIST_SYNC_INTERVAL_SECONDS,
    )

    # One adapter (= one http client) shared across pollers in this process.
    # TODO Phase 2 (multi-market): one adapter per exchange (kr/us/...).
    kr_adapter = KrMarketAdapter()

    # Active pollers, keyed by canonical id "EX:SYM".
    pollers: dict[str, PricePoller] = {}

    scheduler = AsyncIOScheduler(timezone="UTC")

    async def reconcile_watchlist() -> None:
        """Read DB watchlist and add/remove polling jobs to match."""
        async with SessionLocal() as session:
            stmt = select(WatchlistEntry).options(joinedload(WatchlistEntry.instrument))
            entries = (await session.execute(stmt)).scalars().all()

        desired: dict[str, tuple[str, str]] = {}
        for entry in entries:
            inst = entry.instrument
            # Phase 1: KR adapter only. Skip others quietly.
            if inst.exchange != "KR":
                continue
            desired[f"{inst.exchange}:{inst.symbol}"] = (inst.exchange, inst.symbol)

        # Remove jobs for symbols no longer in watchlist
        for key in list(pollers.keys()):
            if key not in desired:
                exchange, symbol = key.split(":", 1)
                job_id = _poller_job_id(exchange, symbol)
                try:
                    scheduler.remove_job(job_id)
                except Exception as exc:  # noqa: BLE001 — job may already be gone
                    log.warning(
                        "worker.remove_job_failed", job=job_id, error=str(exc)
                    )
                pollers.pop(key, None)
                log.info("worker.poller_removed", exchange=exchange, symbol=symbol)

        # Add jobs for new symbols
        for key, (exchange, symbol) in desired.items():
            if key in pollers:
                continue
            poller = PricePoller(
                exchange=exchange,
                symbol=symbol,
                adapter=kr_adapter,
                redis=redis_client,
            )
            pollers[key] = poller
            scheduler.add_job(
                poller.tick,
                "interval",
                seconds=POLL_INTERVAL_SECONDS,
                id=_poller_job_id(exchange, symbol),
                coalesce=True,
                max_instances=1,
                misfire_grace_time=POLL_INTERVAL_SECONDS,
            )
            log.info("worker.poller_added", exchange=exchange, symbol=symbol)

    # Run reconcile once immediately so the initial state is correct without
    # waiting WATCHLIST_SYNC_INTERVAL_SECONDS for the first scheduled run.
    await reconcile_watchlist()

    # Reconcile job: every N seconds thereafter.
    # NOTE: do NOT pass next_run_time=None — APScheduler reads it literally and
    # the job never fires. Omit and let the interval trigger decide.
    scheduler.add_job(
        reconcile_watchlist,
        "interval",
        seconds=WATCHLIST_SYNC_INTERVAL_SECONDS,
        id="watchlist_sync",
        coalesce=True,
        max_instances=1,
    )

    scheduler.start()

    # Wait for SIGINT/SIGTERM so docker compose stop is graceful.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("worker.shutdown", active_pollers=len(pollers))

    scheduler.shutdown(wait=False)
    await kr_adapter.aclose()
    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
