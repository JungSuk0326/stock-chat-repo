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
    venue: str = Query(
        default="KRX",
        description=(
            'Trading venue. KR symbols: "KRX" (정규장, default) or "NXT" '
            "(넥스트레이드 ATS). 통합 view fetches twice."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> PriceSeriesResponse:
    """Return OHLCV bars for `{exchange}:{symbol}` over the last `days` days.

    `venue` filters to one trading venue. Default `KRX` matches pre-NXT
    behavior. The frontend's 통합 tab makes two requests (KRX + NXT) and
    overlays them client-side.
    """
    exchange_norm = exchange.upper().strip()
    symbol_norm = symbol.strip()
    venue_norm = venue.upper().strip()

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
                Price.venue == venue_norm,
                Price.time >= start,
                Price.time <= end,
            )
            .order_by(Price.time.asc())
        )
    ).scalars().all()

    return PriceSeriesResponse(
        instrument=f"{instrument.exchange}:{instrument.symbol}",
        interval=interval,
        venue=venue_norm,
        bars=[PriceBar.model_validate(b) for b in bars],
    )
