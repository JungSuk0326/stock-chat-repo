"""GET /investor-flows/market — market-wide investor-type breakdown.

Reads the `market_investor_flows` table populated by the daily KRX
sweep. The LLM context assembler can read the same table directly
(stays in sync with what the UI sees).

Example:
    GET /investor-flows/market?market=STK&investor_types=private_fund,pension&days=30

`market` defaults to `ALL` (KOSPI+KOSDAQ aggregate). `investor_types`
is comma-separated canonical keys (see app/models/market_investor_flow.py);
omit to return every investor type.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.market_investor_flow import (
    INVESTOR_TYPE_LABELS_KO,
    INVESTOR_TYPES,
    MARKETS,
    MarketInvestorFlow,
)
from app.schemas.market_investor_flow import (
    MarketInvestorFlowItem,
    MarketInvestorFlowListResponse,
)

router = APIRouter(prefix="/investor-flows/market", tags=["investor-flows"])


def _split_csv(s: str | None) -> list[str] | None:
    if s is None or not s.strip():
        return None
    return [v.strip() for v in s.split(",") if v.strip()]


@router.get("", response_model=MarketInvestorFlowListResponse)
async def list_market_investor_flows(
    market: str = Query(default="ALL", description="STK / KSQ / ALL"),
    investor_types: str | None = Query(
        default=None,
        description=(
            "Comma-separated canonical investor keys "
            "(private_fund, pension, foreign, ...). Omit for all."
        ),
    ),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> MarketInvestorFlowListResponse:
    market_norm = market.strip().upper()
    if market_norm not in MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"market must be one of {MARKETS}, got {market_norm!r}",
        )

    types_filter = _split_csv(investor_types)
    if types_filter:
        unknown = [t for t in types_filter if t not in INVESTOR_TYPES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown investor_types: {unknown}. "
                       f"Valid: {list(INVESTOR_TYPES)}",
            )

    end = date.today()
    start = end - timedelta(days=days)

    stmt = (
        select(MarketInvestorFlow)
        .where(
            MarketInvestorFlow.market == market_norm,
            MarketInvestorFlow.trade_date >= start,
            MarketInvestorFlow.trade_date <= end,
        )
        .order_by(
            MarketInvestorFlow.trade_date.desc(),
            MarketInvestorFlow.investor_type,
        )
    )
    if types_filter:
        stmt = stmt.where(MarketInvestorFlow.investor_type.in_(types_filter))

    rows = (await db.execute(stmt)).scalars().all()

    items = [
        MarketInvestorFlowItem(
            trade_date=r.trade_date,
            market=r.market,
            investor_type=r.investor_type,
            investor_label_ko=INVESTOR_TYPE_LABELS_KO.get(
                r.investor_type, r.investor_type
            ),
            net_value=r.net_value,
            buy_value=r.buy_value,
            sell_value=r.sell_value,
        )
        for r in rows
    ]

    return MarketInvestorFlowListResponse(
        market=market_norm,
        investor_types=types_filter,
        count=len(items),
        items=items,
    )
