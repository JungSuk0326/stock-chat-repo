from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Instrument, Price
from app.schemas.price import PriceBar, PriceSeriesResponse

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/{exchange}/{symbol}", response_model=PriceSeriesResponse)
async def get_prices(
    exchange: str,
    symbol: str,
    interval: str = Query(default="1d", description='"1d", "1h", "1m" ...'),
    days: int = Query(default=365, ge=1, le=3650, description="How many days back from now"),
    db: AsyncSession = Depends(get_db),
) -> PriceSeriesResponse:
    """Return OHLCV bars for `{exchange}:{symbol}` over the last `days` days."""
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

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    bars = (
        await db.execute(
            select(Price)
            .where(
                Price.instrument_id == instrument.id,
                Price.interval == interval,
                Price.time >= start,
                Price.time <= end,
            )
            .order_by(Price.time.asc())
        )
    ).scalars().all()

    return PriceSeriesResponse(
        instrument=f"{instrument.exchange}:{instrument.symbol}",
        interval=interval,
        bars=[PriceBar.model_validate(b) for b in bars],
    )
