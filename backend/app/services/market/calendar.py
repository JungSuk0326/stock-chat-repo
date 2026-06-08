"""Market hours + trading-day calendar helpers.

Two venues coexist for KR symbols:
  - KRX (정규장): 09:00-15:30 KST + 시간외 단일가 (we don't poll 시간외)
  - NXT (넥스트레이드 ATS, 2025-03 launch):
      pre-market   08:00-08:50  (NXT only)
      main-market  09:00-15:20  (concurrent with KRX)
      after-market 15:30-20:00  (NXT only)

The polling window is the union of both — 08:00-20:00 KST. Inside that
window we hit Naver every tick; outside it sleeps.

Holiday handling: `is_kr_trading_day(d)` consults a pykrx-derived set of
trading days. Built once on first call (sync, ~1-2s) and refreshed via
`warm_kr_calendar_cache()` at worker startup. Without this, weekday
holidays (현충일, 지방선거일 등) leak phantom 1m bars from the realtime
poller, which then roll up into fake daily candles.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

log = structlog.get_logger()

_KST = ZoneInfo("Asia/Seoul")

KR_OPEN_MINUTES = 9 * 60        # 09:00 KRX 정규장 시작
KR_CLOSE_MINUTES = 15 * 60 + 30  # 15:30 KRX 정규장 마감

# NXT 운영시간 union (pre 08:00 ~ after 20:00)
NXT_OPEN_MINUTES = 8 * 60
NXT_CLOSE_MINUTES = 20 * 60


# ---- Trading-day cache ----
# Module-level set of every KR trading day from last year through end of
# this year. Populated lazily on first `is_kr_trading_day` call (sync,
# blocking) or eagerly via `warm_kr_calendar_cache()` from runner startup.
_trading_days: set[date] | None = None
_cache_lock = threading.Lock()


def _load_trading_days_sync() -> set[date]:
    """pykrx-backed enumeration of trading days for [today-1y, today's year-end].

    Uses Samsung (005930) as a liquidity proxy — any date with an OHLCV
    row is a trading day. ~250 dates/year × 2 years ≈ 500 entries; trivial
    memory. Sync I/O — pykrx is synchronous; wrap with `asyncio.to_thread`
    when calling from async code.
    """
    from pykrx import stock as pykrx_stock

    today = datetime.now(tz=_KST).date()
    start = today.replace(year=today.year - 1, month=1, day=1)
    end = today.replace(month=12, day=31)
    df = pykrx_stock.get_market_ohlcv_by_date(
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
        "005930",
    )
    if df is None or df.empty:
        log.warning("kr_calendar.empty_pykrx_response", start=str(start), end=str(end))
        return set()
    return {idx.date() for idx in df.index}


def _ensure_loaded() -> set[date]:
    global _trading_days
    if _trading_days is not None:
        return _trading_days
    with _cache_lock:
        if _trading_days is None:
            _trading_days = _load_trading_days_sync()
            log.info("kr_calendar.loaded_sync", days=len(_trading_days))
    return _trading_days


async def warm_kr_calendar_cache() -> int:
    """Async startup warm-up. Run from `runner.main()` before scheduler
    starts so the first poller tick doesn't block on pykrx I/O.

    Returns the number of trading days loaded.
    """
    global _trading_days
    days = await asyncio.to_thread(_load_trading_days_sync)
    with _cache_lock:
        _trading_days = days
    log.info("kr_calendar.warmed", days=len(days))
    return len(days)


def is_kr_trading_day(d: date | None = None) -> bool:
    """True iff KRX 정규장 (and NXT) trades on `d`. Defaults to today in KST.

    Weekends short-circuit without touching the cache. For weekdays, falls
    through to the pykrx-derived trading-day set.
    """
    if d is None:
        d = datetime.now(tz=_KST).date()
    if d.weekday() >= 5:  # Sat=5, Sun=6
        return False
    return d in _ensure_loaded()


def kr_market_open(now: datetime | None = None) -> bool:
    """True iff KOSPI/KOSDAQ regular session is open right now.

    Holidays + weekends return False via `is_kr_trading_day`.
    """
    moment = (now or datetime.now(tz=_KST)).astimezone(_KST)
    if not is_kr_trading_day(moment.date()):
        return False
    minutes = moment.hour * 60 + moment.minute
    return KR_OPEN_MINUTES <= minutes < KR_CLOSE_MINUTES


def kr_polling_window_open(now: datetime | None = None) -> bool:
    """True iff the KRX+NXT polling window is open (08:00-20:00 KST,
    trading days only). Realtime poller uses this gate.

    Holidays + weekends return False — without this, the polling worker
    captures phantom snapshots on closed days and the NXT EOD aggregator
    rolls them into fake daily bars.
    """
    moment = (now or datetime.now(tz=_KST)).astimezone(_KST)
    if not is_kr_trading_day(moment.date()):
        return False
    minutes = moment.hour * 60 + moment.minute
    return NXT_OPEN_MINUTES <= minutes < NXT_CLOSE_MINUTES
