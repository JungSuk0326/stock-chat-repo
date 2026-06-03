"""KR investor flow adapter — Naver Finance Mobile.

Endpoint: GET https://m.stock.naver.com/api/stock/{symbol}/trend
  - `pageSize=N` query param controls history depth (verified: 60 works,
    returns ~60 trading days)
  - Response: flat JSON array, newest first
  - Field types: numeric values are STRINGS with commas + sign prefix
    (e.g. "-11,016,912"); ratios have a `%` suffix; dates are YYYYMMDD

Same Naver-unofficial-API caveat as price + news adapters: works fine
for single-user private use, do not redistribute.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import structlog

from app.services.investor_flow.base import InvestorFlowAdapter, InvestorFlowData

log = structlog.get_logger()

_TREND_URL = "https://m.stock.naver.com/api/stock/{symbol}/trend"


class NaverTrendAdapter(InvestorFlowAdapter):
    market_code = "KR"
    source = "naver"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": "Mozilla/5.0 (stock-advisor private)"},
        )

    async def fetch_recent(
        self, symbol: str, *, days: int = 60
    ) -> Sequence[InvestorFlowData]:
        try:
            resp = await self._http.get(
                _TREND_URL.format(symbol=symbol),
                params={"pageSize": days},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning(
                "naver.trend.http_failed", symbol=symbol, error=str(exc)
            )
            return []

        try:
            rows = resp.json()
        except ValueError as exc:
            log.warning("naver.trend.parse_failed", symbol=symbol, error=str(exc))
            return []
        if not isinstance(rows, list):
            return []

        out: list[InvestorFlowData] = []
        for row in rows:
            parsed = _parse_row(row, symbol)
            if parsed is not None:
                out.append(parsed)
        return out

    async def aclose(self) -> None:
        await self._http.aclose()


# ---------- parsing helpers ----------

# "-11,016,912" / "+3,088,203" / "360,500"
_NUMERIC = re.compile(r"^[+\-]?[0-9,]+$")


def _signed_int(s: str | None) -> int | None:
    if s is None:
        return None
    cleaned = s.strip().replace(",", "")
    if not cleaned or not _NUMERIC.match(s.strip()):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _unsigned_int(s: str | None) -> int | None:
    """For close_price etc. — strip commas, no sign expected."""
    v = _signed_int(s)
    return abs(v) if v is not None else None


def _percent(s: str | None) -> Decimal | None:
    """'48.11%' → Decimal('48.11')."""
    if s is None:
        return None
    cleaned = s.strip().rstrip("%").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except (ValueError, ArithmeticError):
        return None


def _parse_bizdate(s: str | None) -> date | None:
    """'YYYYMMDD' → date. Returns None on malformed input."""
    if not s or len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _parse_row(row: dict[str, Any], symbol: str) -> InvestorFlowData | None:
    trade_date = _parse_bizdate(row.get("bizdate"))
    if trade_date is None:
        return None

    foreign = _signed_int(row.get("foreignerPureBuyQuant"))
    institutional = _signed_int(row.get("organPureBuyQuant"))
    individual = _signed_int(row.get("individualPureBuyQuant"))
    # If all three are missing we have no useful row.
    if foreign is None and institutional is None and individual is None:
        return None

    return InvestorFlowData(
        exchange="KR",
        symbol=symbol,
        trade_date=trade_date,
        # Default None → 0 keeps the bigint NOT NULL constraint simple; the
        # only realistic case where one is None is malformed/missing input,
        # in which case 0 is a defensible "no data" sentinel that won't
        # inflate sums or signals.
        foreign_net_volume=foreign or 0,
        foreign_hold_ratio=_percent(row.get("foreignerHoldRatio")),
        institutional_net_volume=institutional or 0,
        individual_net_volume=individual or 0,
        close_price=_unsigned_int(row.get("closePrice")),
    )
