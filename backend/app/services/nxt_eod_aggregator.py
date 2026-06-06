"""NXT daily-bar aggregator from accumulated 1m bars.

NXT(넥스트레이드) has no public per-day OHLCV API — Naver only exposes
NXT's live snapshot via `polling.finance.naver.com`. The realtime worker
captures NXT 1m bars during the 08:00-20:00 KST polling window; this
service rolls those minutes into a daily (1d) bar at EOD.

Effect over time: every NXT trading day adds one new 1d bar. Backfill of
historical NXT data is impossible (no upstream source). After a month
the NXT 1d chart will have ~22 bars; after a year, ~250.

Idempotent: ON CONFLICT (instrument, interval, venue, time) DO UPDATE.
Re-running for the same date overwrites with the latest 1m roll-up.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.models import Instrument, Price, WatchlistEntry

log = structlog.get_logger()


# We treat midnight UTC of the trade date as the 1d bar's `time`, matching
# the convention used by `KrMarketAdapter.fetch_eod_prices` for KRX.
def _utc_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _kst_day_to_utc_range(d: date) -> tuple[datetime, datetime]:
    """KST trade-date [d 00:00, d+1 00:00) → corresponding UTC bounds.

    Naver tags NXT 1m bars with UTC timestamps from the worker's
    `datetime.now(tz=timezone.utc)` at request time. NXT trades
    08:00-20:00 KST (= 23:00 prev UTC ~ 11:00 same UTC). The KST
    day covers a different range, so we map KST → UTC explicitly.
    """
    from zoneinfo import ZoneInfo
    kst = ZoneInfo("Asia/Seoul")
    start_kst = datetime.combine(d, time.min, tzinfo=kst)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc), end_kst.astimezone(timezone.utc)


async def aggregate_nxt_daily_for_symbol(
    *, instrument_id: int, trade_date: date
) -> bool:
    """Roll NXT 1m bars for `trade_date` into one 1d bar. Returns True if a
    bar was UPSERTed (i.e. at least one 1m bar existed for that day)."""
    start_utc, end_utc = _kst_day_to_utc_range(trade_date)

    async with SessionLocal() as session:
        # Aggregate in SQL — cheaper than pulling rows and folding in Python.
        # OHLCV semantics:
        #   open  = close of the first 1m bar in the day
        #   close = close of the last 1m bar in the day
        #   high  = max(high) across the day's 1m bars
        #   low   = min(low)
        #   volume = sum(volume) — each 1m volume is a 60s slice, additive
        first_q = (
            select(Price.close)
            .where(
                Price.instrument_id == instrument_id,
                Price.interval == "1m",
                Price.venue == "NXT",
                Price.time >= start_utc,
                Price.time < end_utc,
            )
            .order_by(Price.time.asc())
            .limit(1)
        )
        last_q = (
            select(Price.close)
            .where(
                Price.instrument_id == instrument_id,
                Price.interval == "1m",
                Price.venue == "NXT",
                Price.time >= start_utc,
                Price.time < end_utc,
            )
            .order_by(Price.time.desc())
            .limit(1)
        )
        agg_q = select(
            func.max(Price.high).label("high"),
            func.min(Price.low).label("low"),
            func.sum(Price.volume).label("volume"),
            func.count().label("n"),
        ).where(
            Price.instrument_id == instrument_id,
            Price.interval == "1m",
            Price.venue == "NXT",
            Price.time >= start_utc,
            Price.time < end_utc,
        )

        first_close = (await session.execute(first_q)).scalar_one_or_none()
        last_close = (await session.execute(last_q)).scalar_one_or_none()
        row = (await session.execute(agg_q)).one()
        n = int(row.n or 0)
        if n == 0 or first_close is None or last_close is None:
            return False

        stmt = pg_insert(Price).values(
            instrument_id=instrument_id,
            interval="1d",
            venue="NXT",
            time=_utc_midnight(trade_date),
            open=Decimal(first_close),
            high=Decimal(row.high),
            low=Decimal(row.low),
            close=Decimal(last_close),
            volume=int(row.volume or 0),
        )
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
    return True


async def aggregate_nxt_daily_watchlist(*, lookback_days: int = 3) -> int:
    """Roll NXT 1m → 1d for every KR watchlist symbol over the last N KST
    days. Lookback covers worker downtime / restarts. Returns the number
    of (symbol × day) bars upserted."""
    from zoneinfo import ZoneInfo
    today_kst = datetime.now(ZoneInfo("Asia/Seoul")).date()
    days = [today_kst - timedelta(days=i) for i in range(lookback_days)]

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(WatchlistEntry.instrument_id, Instrument.symbol)
                .join(Instrument, Instrument.id == WatchlistEntry.instrument_id)
                .where(Instrument.exchange == "KR")
            )
        ).all()

    total = 0
    for inst_id, symbol in rows:
        for d in days:
            try:
                ok = await aggregate_nxt_daily_for_symbol(
                    instrument_id=inst_id, trade_date=d
                )
                if ok:
                    total += 1
            except Exception as exc:  # noqa: BLE001 — log + keep going
                log.warning(
                    "nxt_eod.aggregate_failed",
                    instrument_id=inst_id,
                    symbol=symbol,
                    date=str(d),
                    error=str(exc),
                )

    log.info(
        "nxt_eod.done",
        bars_upserted=total,
        symbols=len(rows),
        lookback_days=lookback_days,
    )
    return total
