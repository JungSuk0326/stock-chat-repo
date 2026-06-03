"""GET /investor-flows/{exchange}/{symbol} — daily 외국인/기관/개인 수급.

The UI's [수급] tab polls this. The LLM context assembler reads the same
table directly (not via this endpoint), so both stay in sync — same
pattern as /disclosures and /news.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Instrument, InvestorFlow
from app.schemas.investor_flow import InvestorFlowItem, InvestorFlowListResponse

router = APIRouter(prefix="/investor-flows", tags=["investor-flows"])


@router.get("/{exchange}/{symbol}", response_model=InvestorFlowListResponse)
async def list_investor_flows(
    exchange: str,
    symbol: str,
    limit: int = Query(default=60, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> InvestorFlowListResponse:
    """Latest `limit` trading days of investor flow for `{exchange}:{symbol}`,
    newest first."""
    exchange_norm = exchange.upper().strip()
    symbol_norm = symbol.strip()

    instrument = (
        await db.execute(
            select(Instrument).where(
                Instrument.exchange == exchange_norm,
                Instrument.symbol == symbol_norm,
            )
        )
    ).scalar_one_or_none()
    if instrument is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument not found: {exchange_norm}:{symbol_norm}",
        )

    rows = (
        (
            await db.execute(
                select(InvestorFlow)
                .where(InvestorFlow.instrument_id == instrument.id)
                .order_by(InvestorFlow.trade_date.desc(), InvestorFlow.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    return InvestorFlowListResponse(
        instrument=f"{instrument.exchange}:{instrument.symbol}",
        count=len(rows),
        items=[InvestorFlowItem.model_validate(r) for r in rows],
    )
