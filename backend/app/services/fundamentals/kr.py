"""KR fundamentals via yfinance (Yahoo backend).

yfinance ticker convention for KR:
  - KOSPI:  005930 → "005930.KS"
  - KOSDAQ: 035720 → "035720.KQ"

We don't have KOSPI/KOSDAQ on the symbol itself in our internal id —
the `instruments` row has `.market`. The adapter accepts both and tries
.KS first, falling back to .KQ if Yahoo doesn't know it.

yfinance .info quirks observed against real KR symbols:
  - `trailingPE` and `priceToBook` often None — we capture `forwardPE`
    as a fallback in a separate column rather than overloading PER.
  - `dividendYield` is already in percent (0.43 == 0.43%), not a ratio.
  - `marketCap` is in instrument currency (KRW), can exceed 32-bit — store as BigInt.
"""

from __future__ import annotations

import asyncio
import math
from decimal import Decimal
from typing import Any

import structlog
import yfinance as yf

from app.services.fundamentals.base import FundamentalsAdapter, FundamentalsData

log = structlog.get_logger()


class YFinanceKrAdapter(FundamentalsAdapter):
    market_code = "KR"

    async def fetch(self, symbol: str) -> FundamentalsData | None:
        # yfinance is synchronous (urllib + requests under the hood) and
        # `.info` blocks on HTTP. to_thread to keep the event loop free.
        try:
            info = await asyncio.to_thread(_fetch_info, symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance.fetch_failed", symbol=symbol, error=str(exc))
            return None
        if not info:
            return None
        return _info_to_data(symbol, info)


def _fetch_info(symbol: str) -> dict[str, Any] | None:
    """Try .KS first; if Yahoo returns an empty/error shape, try .KQ.

    yfinance signals "unknown ticker" by returning a sparse dict (just
    `symbol` echo) — checking for `currency` is the most reliable
    "did we actually get data" probe.
    """
    for suffix in (".KS", ".KQ"):
        ticker = yf.Ticker(f"{symbol}{suffix}")
        info = ticker.info or {}
        if info.get("currency"):  # got real data
            return info
    return None


def _info_to_data(symbol: str, info: dict[str, Any]) -> FundamentalsData:
    return FundamentalsData(
        exchange="KR",
        symbol=symbol,
        per=_dec(info.get("trailingPE")),
        forward_per=_dec(info.get("forwardPE")),
        pbr=_dec(info.get("priceToBook")),
        dividend_yield=_dec(info.get("dividendYield")),
        market_cap=_int(info.get("marketCap")),
        beta=_dec(info.get("beta")),
        sector=_str_or_none(info.get("sector"), 64),
        industry=_str_or_none(info.get("industry"), 128),
    )


def _dec(v: Any) -> Decimal | None:
    """Convert yfinance numeric to Decimal. yfinance occasionally returns
    Infinity (e.g. forwardPE for symbols with 0/negative earnings) — those
    are rejected upstream by Pydantic's `finite_number` validator, so we
    drop to None here."""
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(f):
        return None
    try:
        return Decimal(str(f))
    except (ValueError, TypeError):
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _str_or_none(v: Any, max_len: int) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    return s[:max_len] if s else None
