from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Instrument
from app.schemas.instrument import InstrumentSummary

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("", response_model=list[InstrumentSummary])
async def search_instruments(
    q: str | None = Query(default=None, description="부분 일치 (name 또는 symbol)"),
    market: str | None = Query(default=None, description="KOSPI / KOSDAQ / ..."),
    exchange: str | None = Query(default=None, description="KR / US / ..."),
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[InstrumentSummary]:
    """List instruments with optional fuzzy search. KOSPI ~923 + KOSDAQ ~1726 → 적은 양,
    LIKE로 충분. 사용량 늘면 pg_trgm + GIN index로 업그레이드."""
    stmt = select(Instrument)

    if q:
        q_stripped = q.strip()
        if q_stripped:
            like = f"%{q_stripped}%"
            stmt = stmt.where(
                or_(
                    Instrument.symbol == q_stripped,
                    Instrument.symbol.ilike(like),
                    Instrument.name.ilike(like),
                )
            )

    if market:
        stmt = stmt.where(Instrument.market == market.upper())
    if exchange:
        stmt = stmt.where(Instrument.exchange == exchange.upper())

    # 정확 매치(symbol == q) 먼저 보이도록 정렬은 일단 symbol 오름차순
    stmt = stmt.order_by(Instrument.symbol.asc()).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [InstrumentSummary.model_validate(r) for r in rows]
