"""Fundamentals adapter abstraction.

Phase 1 implements `YFinanceKrAdapter` (Yahoo backend via yfinance).
pykrx's bulk fundamental endpoints are broken upstream as of 1.2.8, so
Yahoo is the practical option even for KR — yfinance maps KOSPI/KOSDAQ
symbols with `.KS` / `.KQ` suffixes internally.

Per-ticker calls are slow (~1 sec each), so the caller is expected to
go through the cache layer (`app/services/fundamentals_sync.py`), not
this adapter directly. 24-hour TTL means most screener runs hit cache
and never touch the network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from pydantic import BaseModel


class FundamentalsData(BaseModel):
    """Snapshot of fundamental metrics for one instrument. All numeric
    fields are optional — yfinance frequently returns None for individual
    metrics depending on the symbol's listing type and freshness."""

    exchange: str
    symbol: str

    per: Decimal | None = None             # trailing PER, preferred if available
    forward_per: Decimal | None = None     # forward PER fallback
    pbr: Decimal | None = None
    dividend_yield: Decimal | None = None  # percent (e.g. 0.43 = 0.43%)
    market_cap: int | None = None          # in instrument's currency
    beta: Decimal | None = None
    sector: str | None = None
    industry: str | None = None


class FundamentalsAdapter(ABC):
    """Per-market fundamentals source.

    Phase 1 ships KR-only via Yahoo. US adapter (also Yahoo via yfinance)
    is trivial to add — same code path with different ticker suffix
    mapping.
    """

    market_code: str

    @abstractmethod
    async def fetch(self, symbol: str) -> FundamentalsData | None:
        """Return fundamentals for `symbol`. None if the symbol can't be
        resolved on the upstream backend (delisted, ticker change, etc.).

        Individual metric fields may still be None even on a successful
        fetch — callers must handle partial data.
        """
        ...

    async def aclose(self) -> None:
        """Optional cleanup. Default no-op."""
        return None
