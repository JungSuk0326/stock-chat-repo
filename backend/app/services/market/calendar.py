"""Market hours helpers.

Phase 1: simple weekday + KST hour check. Holiday handling (R15) is deferred
until we wire pandas_market_calendars or a KRX holiday API.

Two venues coexist for KR symbols:
  - KRX (정규장): 09:00-15:30 KST + 시간외 단일가 (we don't poll 시간외)
  - NXT (넥스트레이드 ATS, 2025-03 launch):
      pre-market   08:00-08:50  (NXT only)
      main-market  09:00-15:20  (concurrent with KRX)
      after-market 15:30-20:00  (NXT only)

The polling window we want is the union of both — 08:00-20:00 KST. Inside
that window we call the Naver polling endpoint; it returns whichever leg(s)
are currently trading.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

KR_OPEN_MINUTES = 9 * 60       # 09:00 KRX 정규장 시작
KR_CLOSE_MINUTES = 15 * 60 + 30  # 15:30 KRX 정규장 마감

# NXT 운영시간 union (pre 08:00 ~ after 20:00)
NXT_OPEN_MINUTES = 8 * 60
NXT_CLOSE_MINUTES = 20 * 60


def kr_market_open(now: datetime | None = None) -> bool:
    """True iff KOSPI/KOSDAQ regular session is open right now.

    Weekend → False. Holidays not yet handled (returns True on holiday weekdays).
    """
    moment = (now or datetime.now(tz=_KST)).astimezone(_KST)
    if moment.weekday() >= 5:  # Sat=5, Sun=6
        return False
    minutes = moment.hour * 60 + moment.minute
    return KR_OPEN_MINUTES <= minutes < KR_CLOSE_MINUTES


def kr_polling_window_open(now: datetime | None = None) -> bool:
    """True iff the KRX+NXT polling window is open (08:00-20:00 KST weekday).

    Used by the realtime poller so it covers NXT pre-market (08:00-08:50)
    and after-market (15:30-20:00) on top of the concurrent main session.
    Inside the window the poller hits Naver every tick; outside it sleeps.
    """
    moment = (now or datetime.now(tz=_KST)).astimezone(_KST)
    if moment.weekday() >= 5:
        return False
    minutes = moment.hour * 60 + moment.minute
    return NXT_OPEN_MINUTES <= minutes < NXT_CLOSE_MINUTES
