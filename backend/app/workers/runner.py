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
from app.services.alert_channels.base import AlertChannel
from app.services.alert_channels.log import LogChannel
from app.services.alert_channels.telegram import TelegramChannel
from app.services.alerts import tick_alert_runner
from app.workers.backup import run_backup
from app.workers.heartbeat import with_heartbeat
from app.services.disclosure.kr import DartAdapter
from app.services.disclosures import (
    sync_corp_codes,
    sync_disclosures_for_symbol,
    sync_disclosures_watchlist,
)
from app.services.discovery import run_all_enabled_screeners
from app.services.fundamentals.kr import YFinanceKrAdapter
from app.services.fundamentals_sync import refresh_watchlist as refresh_fundamentals_watchlist
from app.services.instruments import sync_kr_instruments
from app.services.investor_flow.kr import NaverTrendAdapter
from app.services.investor_flow_sync import (
    sync_investor_flow_for_symbol,
    sync_investor_flow_watchlist,
)
from app.services.market.kr import KrMarketAdapter
from app.services.market.krx_openapi import KrxOpenApiClient
from app.services.market_investor_flow.kr import KrMarketInvestorFlowAdapter
from app.services.market_investor_flow_sync import daily_market_investor_flow_tick
from app.services.prices_openapi import sync_eod_watchlist_via_openapi
from app.services.news.kr import NaverNewsAdapter
from app.services.news_sync import sync_news_for_symbol, sync_news_watchlist
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

# Alert evaluator
ALERT_INTERVAL_SECONDS = 60

# News collector
NEWS_POLL_INTERVAL_SECONDS = 300      # 5 minutes
NEWS_POLL_LIMIT_PER_SYMBOL = 30       # per tick, per symbol
NEWS_BACKFILL_LIMIT = 50              # one-shot on new watchlist join

# Investor flows (외국인/기관/개인 수급)
INVESTOR_FLOW_DAILY_DAYS = 30        # daily sync window — overlaps multiple days
                                      # to absorb worker downtime
INVESTOR_FLOW_BACKFILL_DAYS = 60      # one-shot on new watchlist join

# Market-wide investor flow (KRX 세분류: 사모/연기금/투신 등). Lookback
# overlaps a week so brief worker outages catch up; UNIQUE dedups.
MARKET_INVESTOR_FLOW_LOOKBACK_DAYS = 7


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


def _build_alert_channel(settings) -> AlertChannel:
    """Pick the alert delivery channel based on settings, with safe
    fallbacks so a half-configured deploy still boots."""
    choice = (settings.ALERT_CHANNEL or "log").strip().lower()
    if choice == "telegram":
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            return TelegramChannel(
                bot_token=settings.TELEGRAM_BOT_TOKEN,
                chat_id=settings.TELEGRAM_CHAT_ID,
            )
        log.warning(
            "worker.alert_channel.telegram_unconfigured",
            note="TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing — falling back to log",
        )
        return LogChannel()
    if choice != "log":
        log.warning(
            "worker.alert_channel.unknown",
            choice=choice,
            note="unknown ALERT_CHANNEL value — falling back to log",
        )
    return LogChannel()


async def backfill_news(
    adapter: NaverNewsAdapter, exchange: str, symbol: str
) -> None:
    """One-shot news backfill for a single instrument. Fire-and-forget by
    reconcile_watchlist on new symbols. Naver returns newest first so a
    single page (NEWS_BACKFILL_LIMIT items) covers the typical user
    expectation of "show me recent news"."""
    log.info(
        "worker.news_backfill_started",
        exchange=exchange,
        symbol=symbol,
        limit=NEWS_BACKFILL_LIMIT,
    )
    try:
        count = await sync_news_for_symbol(
            adapter,
            exchange=exchange,
            symbol=symbol,
            limit=NEWS_BACKFILL_LIMIT,
        )
        log.info(
            "worker.news_backfill_done",
            exchange=exchange,
            symbol=symbol,
            new=count,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "worker.news_backfill_failed",
            exchange=exchange,
            symbol=symbol,
            error=str(exc),
        )


async def backfill_investor_flow(
    adapter: NaverTrendAdapter, exchange: str, symbol: str
) -> None:
    """One-shot 60-day investor flow backfill on new watchlist join.
    Fire-and-forget alongside the price + disclosure + news backfills."""
    log.info(
        "worker.investor_flow_backfill_started",
        exchange=exchange,
        symbol=symbol,
        days=INVESTOR_FLOW_BACKFILL_DAYS,
    )
    try:
        count = await sync_investor_flow_for_symbol(
            adapter,
            exchange=exchange,
            symbol=symbol,
            days=INVESTOR_FLOW_BACKFILL_DAYS,
        )
        log.info(
            "worker.investor_flow_backfill_done",
            exchange=exchange,
            symbol=symbol,
            new=count,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "worker.investor_flow_backfill_failed",
            exchange=exchange,
            symbol=symbol,
            error=str(exc),
        )


async def investor_flow_daily_tick(adapter: NaverTrendAdapter) -> None:
    """Daily sweep across the KR watchlist. 30-day window absorbs short
    worker downtime — UNIQUE(instrument_id, trade_date) does the dedup."""
    try:
        new = await sync_investor_flow_watchlist(
            adapter, days=INVESTOR_FLOW_DAILY_DAYS
        )
        if new:
            log.info("worker.investor_flow_daily.new", new=new)
    except Exception as exc:  # noqa: BLE001
        log.warning("worker.investor_flow_daily.failed", error=str(exc))


async def news_poll_tick(adapter: NaverNewsAdapter) -> None:
    """Every-N-minutes sweep across the watchlist. UNIQUE(source, source_id)
    handles dedup at the DB layer so we don't need a high-water-mark."""
    try:
        new = await sync_news_watchlist(adapter, limit=NEWS_POLL_LIMIT_PER_SYMBOL)
        if new:
            log.info("worker.news_poll.new", new=new)
    except Exception as exc:  # noqa: BLE001
        log.warning("worker.news_poll.failed", error=str(exc))


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

    # Alert delivery channel. "telegram" falls back to "log" if the bot
    # token / chat id aren't both configured — the operator gets a clear
    # warning instead of a crash loop.
    alert_channel: AlertChannel = _build_alert_channel(settings)
    log.info("worker.alert_channel", channel=alert_channel.name)

    # News adapter — Naver Mobile (unofficial). Always available since it
    # needs no API key, but rate limits are unknown — the 5-min interval
    # keeps us well below visible thresholds.
    news_adapter = NaverNewsAdapter()

    # Fundamentals adapter — yfinance backend. No API key, but rate-limited
    # implicitly by Yahoo; refresh_fundamentals_watchlist caps concurrency.
    fundamentals_adapter = YFinanceKrAdapter()

    # Investor flow adapter — Naver mobile trend endpoint. Same unofficial-API
    # caveat as prices/news; one HTTP call per symbol covers ~60 trading days.
    investor_flow_adapter = NaverTrendAdapter()

    # Market-wide investor breakdown (사모/연기금/투신 etc.) via pykrx.
    # Requires KRX_ID/KRX_PW (set in .env). pykrx logs the login attempt on
    # first call; if creds are missing the daily job simply emits zero rows.
    market_investor_flow_adapter = KrMarketInvestorFlowAdapter()
    if settings.KRX_ID and settings.KRX_PW:
        log.info("worker.krx_login.configured")
    else:
        log.warning(
            "worker.krx_login.missing",
            note=(
                "set KRX_ID / KRX_PW in .env to enable market-wide investor "
                "breakdown (사모/연기금/투신 etc.)"
            ),
        )

    # KRX OpenAPI (official REST). When configured, the daily EOD sweep
    # uses this instead of pykrx. Backfill / instrument master / investor
    # flow are unchanged. None when no key — `sync_eod_watchlist_via_openapi`
    # would early-out, so we just skip scheduling that job.
    krx_openapi_client: KrxOpenApiClient | None = None
    if settings.KRX_OPENAPI_KEY:
        krx_openapi_client = KrxOpenApiClient(api_key=settings.KRX_OPENAPI_KEY)
        log.info("worker.krx_openapi.configured")
    else:
        log.info(
            "worker.krx_openapi.fallback_pykrx",
            note="KRX_OPENAPI_KEY not set — daily EOD sweep falls back to pykrx",
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
            # And news — Naver returns most-recent first, so one page is
            # enough to populate the UI list right away.
            asyncio.create_task(
                backfill_news(news_adapter, exchange, symbol),
                name=f"backfill_news_{exchange}_{symbol}",
            )
            # Investor flow — 60d window seeds a chart-friendly history.
            asyncio.create_task(
                backfill_investor_flow(investor_flow_adapter, exchange, symbol),
                name=f"backfill_investor_flow_{exchange}_{symbol}",
            )

    # Run reconcile once immediately so the initial state is correct without
    # waiting WATCHLIST_SYNC_INTERVAL_SECONDS for the first scheduled run.
    await reconcile_watchlist()

    # Reconcile job: every N seconds thereafter.
    # NOTE: do NOT pass next_run_time=None — APScheduler reads it literally and
    # the job never fires. Omit and let the interval trigger decide.
    # All scheduled jobs below are wrapped in with_heartbeat so /health can
    # show "last successful run" per job (R1).
    scheduler.add_job(
        with_heartbeat(redis_client, "watchlist_sync", reconcile_watchlist),
        "interval",
        seconds=WATCHLIST_SYNC_INTERVAL_SECONDS,
        id="watchlist_sync",
        coalesce=True,
        max_instances=1,
    )

    # Daily EOD sync at 16:00 KST (KOSPI closes 15:30; +30min buffer for KRX
    # to publish). Last `EOD_SYNC_LOOKBACK_DAYS` days are re-pulled to cover
    # worker-downtime gaps; idempotent UPSERT so re-runs are cheap.
    #
    # Routing: prefer KRX OpenAPI (정식 REST API, fanout per-date over all
    # listings, 1 call per (date × market)). Falls back to pykrx (per-symbol
    # over date range) when KRX_OPENAPI_KEY isn't configured.
    if krx_openapi_client is not None:
        scheduler.add_job(
            with_heartbeat(
                redis_client,
                "eod_sync_daily",
                sync_eod_watchlist_via_openapi,
            ),
            CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
            args=[krx_openapi_client, EOD_SYNC_LOOKBACK_DAYS],
            id="eod_sync_daily",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
    else:
        scheduler.add_job(
            with_heartbeat(redis_client, "eod_sync_daily", sync_eod_watchlist),
            CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
            args=[kr_adapter, EOD_SYNC_LOOKBACK_DAYS],
            id="eod_sync_daily",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )

    # Daily instrument-master refresh at 06:00 KST (well before market open).
    # Catches new listings and name changes. Idempotent UPSERT.
    scheduler.add_job(
        with_heartbeat(redis_client, "instruments_sync_daily", sync_kr_instruments),
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
            with_heartbeat(redis_client, "dart_corp_code_sync_daily", sync_corp_codes),
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
            with_heartbeat(redis_client, "disclosure_poll", disclosure_poll_tick),
            "interval",
            seconds=DISCLOSURE_POLL_INTERVAL_SECONDS,
            args=[dart_adapter],
            id="disclosure_poll",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=DISCLOSURE_POLL_INTERVAL_SECONDS,
        )

    # Per-minute alert evaluator. max_instances=1 means we never overlap
    # evaluations even if a tick runs long; cooldown_minutes on each rule
    # provides the user-facing dedup window.
    scheduler.add_job(
        with_heartbeat(redis_client, "alert_runner", tick_alert_runner),
        "interval",
        seconds=ALERT_INTERVAL_SECONDS,
        args=[redis_client, alert_channel],
        id="alert_runner",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=ALERT_INTERVAL_SECONDS,
    )

    # Daily DB backup at 03:30 KST — quiet hour, before the morning
    # instrument/corp_code refreshes. Failures land in /health as
    # `backup_daily` heartbeat (R5).
    scheduler.add_job(
        with_heartbeat(redis_client, "backup_daily", run_backup),
        CronTrigger(hour=3, minute=30, timezone="Asia/Seoul"),
        id="backup_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # News sweep every 5 minutes across the watchlist (Top6).
    scheduler.add_job(
        with_heartbeat(redis_client, "news_poll", news_poll_tick),
        "interval",
        seconds=NEWS_POLL_INTERVAL_SECONDS,
        args=[news_adapter],
        id="news_poll",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=NEWS_POLL_INTERVAL_SECONDS,
    )

    # Daily fundamentals refresh at 17:00 KST (after EOD price sync at 16:00,
    # so any PER/PBR/marketCap re-computations Yahoo did during the day
    # are settled). Watchlist-only — wider universes are pulled on-demand
    # by screener runs (Top7). Cache TTL is 24h either way.
    scheduler.add_job(
        with_heartbeat(
            redis_client,
            "fundamentals_refresh_daily",
            refresh_fundamentals_watchlist,
        ),
        CronTrigger(hour=17, minute=0, timezone="Asia/Seoul"),
        args=[fundamentals_adapter],
        id="fundamentals_refresh_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Daily screener run at 17:30 KST — after EOD prices (16:00) and
    # fundamentals refresh (17:00). Walks every enabled screener and
    # writes candidates. On-demand re-runs available via REST in Phase C.
    scheduler.add_job(
        with_heartbeat(
            redis_client,
            "screener_run_daily",
            run_all_enabled_screeners,
        ),
        CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
        args=[fundamentals_adapter],
        id="screener_run_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Daily investor flow sweep at 16:30 KST — Naver publishes the day's
    # 외국인/기관/개인 수급 right after the 15:30 KRX close. 30-day window
    # overlaps multiple sessions so a half-day downtime still catches up.
    scheduler.add_job(
        with_heartbeat(
            redis_client,
            "investor_flow_sync_daily",
            investor_flow_daily_tick,
        ),
        CronTrigger(hour=16, minute=30, timezone="Asia/Seoul"),
        args=[investor_flow_adapter],
        id="investor_flow_sync_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Daily KRX market-wide investor breakdown at 16:45 KST — KRX publishes
    # the daily aggregate shortly after 16:00. Sweep covers all three
    # market codes (STK/KSQ/ALL). Heartbeat lands in /health; KRX login
    # required (silent no-op on empty data otherwise).
    scheduler.add_job(
        with_heartbeat(
            redis_client,
            "market_investor_flow_daily",
            daily_market_investor_flow_tick,
        ),
        CronTrigger(hour=16, minute=45, timezone="Asia/Seoul"),
        kwargs={"lookback_days": MARKET_INVESTOR_FLOW_LOOKBACK_DAYS},
        args=[market_investor_flow_adapter],
        id="market_investor_flow_daily",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
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
    await news_adapter.aclose()
    await fundamentals_adapter.aclose()
    await investor_flow_adapter.aclose()
    if krx_openapi_client is not None:
        await krx_openapi_client.aclose()
    await alert_channel.aclose()
    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
