"""Market hours helpers.

Phase 1: simple weekday + KST hour check. Holiday handling (R15) is deferred
until we wire pandas_market_calendars or a KRX holiday API.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

KR_OPEN_MINUTES = 9 * 60       # 09:00
KR_CLOSE_MINUTES = 15 * 60 + 30  # 15:30


def kr_market_open(now: datetime | None = None) -> bool:
    """True iff KOSPI/KOSDAQ regular session is open right now.

    Weekend → False. Holidays not yet handled (returns True on holiday weekdays).
    """
    moment = (now or datetime.now(tz=_KST)).astimezone(_KST)
    if moment.weekday() >= 5:  # Sat=5, Sun=6
        return False
    minutes = moment.hour * 60 + moment.minute
    return KR_OPEN_MINUTES <= minutes < KR_CLOSE_MINUTES
