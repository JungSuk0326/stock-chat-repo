"""Daily EOD price sweep via KRX OpenAPI (official REST).

Companion to `app/services/prices.py` (which keeps pykrx as the data
source for per-symbol backfill). This module owns the OTHER direction:

  per-date fanout — fetch EVERY listed stock for one trade date, filter
  to watchlist symbols, UPSERT.

Why two paths:
  - Backfill (new watchlist join, 1 year): pykrx is 1 call/symbol. OpenAPI
    would be 250+ calls/symbol. pykrx wins.
  - Daily sweep (16:00 KST, lookback ~7 days): OpenAPI is 2×lookback calls
    total regardless of watchlist size. Cleaner + 정식 경로.

If KRX_OPENAPI_KEY isn't set, the worker falls back to the pykrx-based
`sync_eod_watchlist` from `app/services/prices.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app.core.db import SessionLocal
from app.models import Instrument, Price, WatchlistEntry
from app.services.market.krx_openapi import (
    KrxOpenApiClient,
    KrxOpenApiError,
    StockDailyRow,
    utc_midnight_of,
)


@dataclass
class OpenApiSyncResult:
    """Outcome of one EOD sweep. Used by the runner-side fallback to decide
    whether the OpenAPI path actually worked or should silently route to
    pykrx. `successful_fetches == 0` means every per-date call failed and
    the caller should fall back."""

    rows_upserted: int
    auth_failures: int
    other_failures: int
    successful_fetches: int

log = structlog.get_logger()

# Markets we sweep daily. KONEX skipped — out of scope for personal
# investing, and we'd be making API calls for ~150 illiquid names.
_DAILY_MARKETS: tuple[str, ...] = ("STK", "KSQ")


async def _watchlist_kr_symbol_map() -> dict[str, int]:
    """{ symbol: instrument_id } for every KR row currently in the watchlist.
    Empty dict = nothing to sync."""
    async with SessionLocal() as session:
        entries = (
            await session.execute(
                select(WatchlistEntry).options(
                    joinedload(WatchlistEntry.instrument)
                )
            )
        ).scalars().all()
    return {
        e.instrument.symbol: e.instrument.id
        for e in entries
        if e.instrument and e.instrument.exchange == "KR"
    }


async def sync_eod_watchlist_via_openapi(
    client: KrxOpenApiClient,
    lookback_days: int = 7,
) -> OpenApiSyncResult:
    """Daily sweep: fetch [today-lookback, today] for every market, filter
    to watchlist, UPSERT. Returns a result with row + failure counts so the
    caller can decide whether to fall back to pykrx.

    Idempotent (UPSERT). Misfire-safe — re-running picks up any holiday
    or worker-downtime gaps without manual intervention."""
    if not client.configured:
        log.info("eod_openapi.skip", reason="no_api_key")
        return OpenApiSyncResult(0, 0, 0, 0)

    symbol_to_id = await _watchlist_kr_symbol_map()
    if not symbol_to_id:
        log.info("eod_openapi.skip", reason="empty_watchlist")
        return OpenApiSyncResult(0, 0, 0, 0)

    end = date.today()
    start = end - timedelta(days=lookback_days)

    matched_rows: list[StockDailyRow] = []
    auth_failures = 0
    other_failures = 0
    successful_fetches = 0
    # Iterate dates oldest → newest so logs make sense if a holiday in the
    # middle returns empty; we can still see the surrounding day worked.
    cur = start
    while cur <= end:
        for market in _DAILY_MARKETS:
            try:
                rows = await client.fetch_stock_daily(cur, market)
            except KrxOpenApiError as exc:
                # One failed market on one day shouldn't abort the whole
                # sweep — log and keep going. Classify so the caller can
                # tell "key is broken" (auth) from "transient network".
                msg = str(exc)
                if "401" in msg or "AUTH_KEY" in msg:
                    auth_failures += 1
                else:
                    other_failures += 1
                log.warning(
                    "eod_openapi.fetch_failed",
                    market=market,
                    date=str(cur),
                    error=msg,
                )
                continue
            successful_fetches += 1
            if not rows:
                # Weekend / holiday is the common reason. Don't spam.
                continue
            for r in rows:
                if r.symbol in symbol_to_id:
                    matched_rows.append(r)
        cur += timedelta(days=1)

    if not matched_rows:
        log.info(
            "eod_openapi.no_matches",
            start=str(start),
            end=str(end),
            watchlist_size=len(symbol_to_id),
            auth_failures=auth_failures,
            other_failures=other_failures,
            successful_fetches=successful_fetches,
        )
        return OpenApiSyncResult(0, auth_failures, other_failures, successful_fetches)

    # KRX OpenAPI returns 정규장 trades only — venue="KRX". NXT bars come
    # from `nxt_eod_aggregator`. The conflict spec must include `venue`
    # because the prices PK is (instrument_id, interval, venue, time);
    # a 3-column spec has no matching unique constraint and Postgres rejects.
    upsert_payload = [
        {
            "instrument_id": symbol_to_id[r.symbol],
            "interval": "1d",
            "venue": "KRX",
            "time": utc_midnight_of(r.bas_dd),
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in matched_rows
    ]

    async with SessionLocal() as session:
        stmt = pg_insert(Price).values(upsert_payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "interval", "venue", "time"],
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
        "eod_openapi.done",
        start=str(start),
        end=str(end),
        rows=len(matched_rows),
        watchlist_size=len(symbol_to_id),
        auth_failures=auth_failures,
        other_failures=other_failures,
        successful_fetches=successful_fetches,
    )
    return OpenApiSyncResult(
        rows_upserted=len(matched_rows),
        auth_failures=auth_failures,
        other_failures=other_failures,
        successful_fetches=successful_fetches,
    )
