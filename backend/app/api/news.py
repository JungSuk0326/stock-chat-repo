"""GET /news/{exchange}/{symbol} — recent news headlines.

The UI's news tab polls this. The LLM context assembler reads the same
table directly (not via this endpoint), so the two paths share the
underlying table and stay in sync automatically — same pattern as
/disclosures.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Instrument, NewsItem
from app.schemas.news import NewsItemDTO, NewsListResponse

router = APIRouter(prefix="/news", tags=["news"])


@router.get("/{exchange}/{symbol}", response_model=NewsListResponse)
async def list_news(
    exchange: str,
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> NewsListResponse:
    """Latest `limit` news items for `{exchange}:{symbol}`, newest first."""
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
                select(NewsItem)
                .where(NewsItem.instrument_id == instrument.id)
                .order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    return NewsListResponse(
        instrument=f"{instrument.exchange}:{instrument.symbol}",
        count=len(rows),
        items=[NewsItemDTO.model_validate(r) for r in rows],
    )
