from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.instrument import InstrumentSummary


class WatchlistItem(BaseModel):
    """One row in the user's watchlist, with the joined instrument inlined."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int
    added_at: datetime
    instrument: InstrumentSummary


class AddToWatchlistRequest(BaseModel):
    """Add by canonical key (exchange + symbol). API resolves to instrument_id."""

    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    position: int = 0
