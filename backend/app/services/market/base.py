from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class InstrumentData(BaseModel):
    """Adapter-level instrument record. Decoupled from the ORM model so adapters
    can be tested without DB and so the same shape works across markets.
    """

    exchange: str = Field(..., max_length=8)  # KR, US, JP, etc.
    symbol: str = Field(..., max_length=32)
    country: str = Field(..., min_length=2, max_length=2)  # ISO 3166-1 alpha-2
    currency: str = Field(..., min_length=3, max_length=3)  # ISO 4217
    market: str | None = Field(default=None, max_length=16)  # KOSPI, KOSDAQ, NYSE...
    name: str | None = Field(default=None, max_length=255)
    isin: str | None = Field(default=None, min_length=12, max_length=12)


class PriceData(BaseModel):
    """Adapter-level OHLCV bar.

    `time` is the bar's identifying timestamp in UTC. For daily bars, the
    convention is midnight UTC of the trading date.
    """

    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class MarketAdapter(ABC):
    """Per-market data source adapter.

    Each market (KR, US, ...) implements this. Higher-level services depend on
    the abstract interface only.
    """

    #: Internal market code, matches the `exchange` field of InstrumentData
    market_code: str

    @abstractmethod
    async def fetch_instruments(self) -> Sequence[InstrumentData]:
        """Return the full tradable instrument master for this market."""
        ...

    @abstractmethod
    async def fetch_eod_prices(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[PriceData]:
        """Return daily OHLCV bars for `symbol` between [start, end] inclusive.

        Empty Sequence if no trading days in range.
        """
        ...
