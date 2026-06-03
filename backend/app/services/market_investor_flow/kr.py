"""KRX market-wide investor flow adapter via pykrx.

`pykrx.stock.get_market_trading_value_by_date(..., detail=True)` returns a
daily-indexed DataFrame with these Korean column labels (in this exact
order — see pykrx/website/krx/market/wrap.py):

    금융투자, 보험, 투신, 사모, 은행, 기타금융, 연기금,
    기타법인, 개인, 외국인, 기타외국인, 전체

We project those onto the canonical investor_type keys defined in
`app/models/market_investor_flow.py` and emit one MarketInvestorFlowRow
per (date, investor). "전체" is dropped — that's the row sum, not an
investor.

Requires KRX login. pykrx reads KRX_ID / KRX_PW from os.environ via
`build_krx_session()` in `pykrx.website.comm.auth`; if those aren't
set, KRX blocks the detail endpoint with a "LOGOUT" response and the
DataFrame comes back empty.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any

import structlog
from pandas import DataFrame
from pykrx import stock

from app.services.market_investor_flow.base import (
    MarketInvestorFlowAdapter,
    MarketInvestorFlowRow,
)

log = structlog.get_logger()


# Korean column label → canonical investor_type. Keep in lock-step with
# MarketInvestorFlow.INVESTOR_TYPES.
_LABEL_TO_KEY: dict[str, str] = {
    "금융투자": "financial_investment",
    "보험": "insurance",
    "투신": "investment_trust",
    "사모": "private_fund",
    "은행": "bank",
    "기타금융": "other_finance",
    "연기금": "pension",
    "기타법인": "other_corp",
    "개인": "individual",
    "외국인": "foreign",
    "기타외국인": "other_foreign",
}

# KRX mktId ↔ pykrx friendly name (the high-level pykrx wrapper takes
# the friendly name; we store the mktId so it matches KRX's own
# vocabulary and downstream tools can pass mktId directly).
_MKTID_TO_PYKRX: dict[str, str] = {
    "STK": "KOSPI",
    "KSQ": "KOSDAQ",
    "ALL": "ALL",
}


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


class KrMarketInvestorFlowAdapter(MarketInvestorFlowAdapter):
    market_code = "KR"
    source = "krx"

    async def fetch_daily(
        self, start: date, end: date, *, market: str
    ) -> Sequence[MarketInvestorFlowRow]:
        if market not in _MKTID_TO_PYKRX:
            log.warning("krx.market_investor_flow.bad_market", market=market)
            return []

        pykrx_market = _MKTID_TO_PYKRX[market]
        fromdate, todate = _yyyymmdd(start), _yyyymmdd(end)

        # pykrx is blocking; run in the default executor so the worker
        # loop stays responsive. Errors raised inside the call surface
        # as exceptions here.
        try:
            df: DataFrame = await asyncio.to_thread(
                stock.get_market_trading_value_by_date,
                fromdate,
                todate,
                pykrx_market,
                etf=False,
                etn=False,
                elw=False,
                on="순매수",
                detail=True,
            )
        except Exception as exc:  # noqa: BLE001 — pykrx wraps many errors
            log.warning(
                "krx.market_investor_flow.fetch_failed",
                market=market,
                error=str(exc),
                fromdate=fromdate,
                todate=todate,
            )
            return []

        if df is None or df.empty:
            return []

        rows: list[MarketInvestorFlowRow] = []
        for trade_dt, row in df.iterrows():
            # pykrx indexes by datetime; .date() normalizes to date object.
            td = trade_dt.date() if hasattr(trade_dt, "date") else trade_dt
            for label, investor_key in _LABEL_TO_KEY.items():
                val = row.get(label)
                if val is None:
                    continue
                rows.append(
                    MarketInvestorFlowRow(
                        trade_date=td,
                        market=market,
                        investor_type=investor_key,
                        net_value=int(val),
                        source="krx",
                    )
                )
        return rows


def _coerce_int(v: Any) -> int | None:
    """Tolerant int coercion for pykrx values that may arrive as numpy
    int64, str, or NaN."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
