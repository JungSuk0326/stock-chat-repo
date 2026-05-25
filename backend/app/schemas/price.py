from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PriceBar(BaseModel):
    """One OHLCV bar in API responses. Times are UTC ISO 8601."""

    model_config = ConfigDict(from_attributes=True)

    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class PriceSeriesResponse(BaseModel):
    instrument: str  # canonical id, e.g. "KR:005930"
    interval: str  # "1d", "1h", "1m" ...
    bars: list[PriceBar]
