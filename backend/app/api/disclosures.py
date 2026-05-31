"""GET /disclosures/{exchange}/{symbol} — recent disclosures for an instrument.

The UI's DisclosurePanel polls this. The LLM context assembler reads the same
data from DB directly (not via this endpoint), so the two paths share the
underlying table and stay in sync automatically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Disclosure, Instrument
from app.schemas.disclosure import DisclosureItem, DisclosureListResponse

router = APIRouter(prefix="/disclosures", tags=["disclosures"])


@router.get("/{exchange}/{symbol}", response_model=DisclosureListResponse)
async def list_disclosures(
    exchange: str,
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> DisclosureListResponse:
    """Latest `limit` disclosures for `{exchange}:{symbol}`, newest first."""
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
                select(Disclosure)
                .where(Disclosure.instrument_id == instrument.id)
                .order_by(Disclosure.filed_at.desc(), Disclosure.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    return DisclosureListResponse(
        instrument=f"{instrument.exchange}:{instrument.symbol}",
        count=len(rows),
        items=[DisclosureItem.model_validate(r) for r in rows],
    )
