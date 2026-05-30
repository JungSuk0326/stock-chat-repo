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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.core.redis_client import redis_client
from app.models import WatchlistEntry
from app.services.disclosure.kr import DartAdapter
from app.services.disclosures import (
    sync_corp_codes,
    sync_disclosures_for_symbol,
    sync_disclosures_watchlist,
)
from app.services.instruments import sync_kr_instruments
from app.services.market.kr import KrMarketAdapter
from app.services.prices import sync_eod_prices, sync_eod_watchlist
from app.workers.price_poller import PricePoller

_KST = ZoneInfo("Asia/Seoul")

log = structlog.get_logger()

POLL_INTERVAL_SECONDS = 2
WATCHLIST_SYNC_INTERVAL_SECONDS = 30
BACKFILL_DAYS = 365
EOD_SYNC_LOOKBACK_DAYS = 7

# Disclosure worker timings
DISCLOSURE_POLL_INTERVAL_SECONDS = 60
DISCLOSURE_BACKFILL_DAYS = 180          # 6 months
DISCLOSURE_POLL_LOOKBACK_DAYS = 2       # today + yesterday — covers post-midnight crossover
                                        # and brief worker downtime in one shot


def _poller_job_id(exchange: str, symbol: str) -> str:
    return f"price_poller_{exchange}_{symbol}"


async def backfill_eod(adapter: KrMarketAdapter, exchange: str, symbol: str) -> None:
    """1-year EOD backfill for a single instrument.

    Fire-and-forget by `reconcile_watchlist` whenever a new symbol joins the
    watchlist. UPSERT-idempotent — repeated runs are safe.
    """
    end = date.today()
    start = end - timedelta(days=BACKFILL_DAYS)
    log.info("worker.backfill_started", exchange=exchange, symbol=symbol, days=BACKFILL_DAYS)
    try:
        count = await sync_eod_prices(adapter, exchange, symbol, start, end)
        log.info("worker.backfill_done", exchange=exchange, symbol=symbol, bars=count)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "worker.backfill_failed",
            exchange=exchange,
            symbol=symbol,
            error=str(exc),
        )


def _kst_today() -> date:
    """KST-local date — DART date filters are KST."""
    return datetime.now(_KST).date()


async def backfill_disclosures(
    adapter: DartAdapter, exchange: str, symbol: str
) -> None:
    """6-month disclosure backfill for a single instrument.

    Fire-and-forget by reconcile_watchlist on new symbols. Silent no-op when
    DART_API_KEY isn't configured. Idempotent.
    """
    if not adapter.configured:
        return
    end = _kst_today()
    start = end - timedelta(days=DISCLOSURE_BACKFILL_DAYS)
    log.info(
        "worker.disclosure_backfill_started",
        exchange=exchange,
        symbol=symbol,
        days=DISCLOSURE_BACKFILL_DAYS,
    )
    try:
        count = await sync_disclosures_for_symbol(
            adapter,
            exchange=exchange,
            symbol=symbol,
            start=start,
            end=end,
        )
        log.info(
            "worker.disclosure_backfill_done",
            exchange=exchange,
            symbol=symbol,
            new=count,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "worker.disclosure_backfill_failed",
            exchange=exchange,
            symbol=symbol,
            error=str(exc),
        )


async def disclosure_poll_tick(adapter: DartAdapter) -> None:
    """Per-minute disclosure poll across the entire KR watchlist.

    Date range = [today-1, today] KST so post-midnight crossings and brief
    worker downtime are covered without needing a high-water-mark.
    UNIQUE(source, source_id) handles dedup in the DB.
    """
    if not adapter.configured:
        return
    end = _kst_today()
    start = end - timedelta(days=DISCLOSURE_POLL_LOOKBACK_DAYS - 1)
    try:
        new = await sync_disclosures_watchlist(adapter, start=start, end=end)
        if new:
            log.info("worker.disclosure_poll.new", new=new)
    except Exception as exc:  # noqa: BLE001
        log.warning("worker.disclosure_poll.failed", error=str(exc))


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

    # DART disclosure adapter. Silent no-op when DART_API_KEY isn't set, so
    # the worker still boots cleanly on a fresh install.
    dart_adapter = DartAdapter(api_key=settings.DART_API_KEY)
    if dart_adapter.configured:
        log.info("worker.dart.configured")
    else:
        log.warning(
            "worker.dart.no_api_key",
            note="set DART_API_KEY in .env to enable disclosure ingestion",
        )

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
            # Fire-and-forget EOD backfill so newly-watched symbols get their
            # 1-year history without manual sync_prices invocation.
            # Idempotent (UPSERT) — safe even if data already exists.
            asyncio.create_task(
                backfill_eod(kr_adapter, exchange, symbol),
                name=f"backfill_{exchange}_{symbol}",
            )
            # Same pattern for disclosures (6 months). Independent task so a
            # slow DART backfill doesn't block the price poller from starting.
            asyncio.create_task(
                backfill_disclosures(dart_adapter, exchange, symbol),
                name=f"backfill_disclosures_{exchange}_{symbol}",
            )

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

    # Daily EOD sync at 16:00 KST (KOSPI closes 15:30; +30min buffer for pykrx).
    # Last `EOD_SYNC_LOOKBACK_DAYS` days are re-pulled for every watchlist
    # symbol. Idempotent UPSERT, so catching up after worker downtime is free.
    scheduler.add_job(
        sync_eod_watchlist,
        CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
        args=[kr_adapter, EOD_SYNC_LOOKBACK_DAYS],
        id="eod_sync_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,  # if worker was down at 16:00, still run within 1h
    )

    # Daily instrument-master refresh at 06:00 KST (well before market open).
    # Catches new listings and name changes. Idempotent UPSERT.
    scheduler.add_job(
        sync_kr_instruments,
        CronTrigger(hour=6, minute=0, timezone="Asia/Seoul"),
        args=[kr_adapter],
        id="instruments_sync_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Daily DART corp_code refresh at 05:30 KST (R11). Runs before the
    # 06:00 instrument sync so any newly listed firms can be mapped right
    # away. Silent no-op if DART_API_KEY isn't configured.
    if dart_adapter.configured:
        scheduler.add_job(
            sync_corp_codes,
            CronTrigger(hour=5, minute=30, timezone="Asia/Seoul"),
            args=[dart_adapter],
            id="dart_corp_code_sync_daily",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        # Per-minute disclosure poll across the watchlist. Date range is
        # [today-1, today] KST inside the tick — see disclosure_poll_tick.
        scheduler.add_job(
            disclosure_poll_tick,
            "interval",
            seconds=DISCLOSURE_POLL_INTERVAL_SECONDS,
            args=[dart_adapter],
            id="disclosure_poll",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=DISCLOSURE_POLL_INTERVAL_SECONDS,
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
    await dart_adapter.aclose()
    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
