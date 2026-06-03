"""Investor flow adapter abstraction.

Phase 1 ships KR-only via Naver Finance Mobile API. Phase 2 KR adapters
might fall back to KRX 정보데이터 (when pykrx works again) or scrape the
Naver desktop page. For US (Phase 2), there's no exact equivalent —
13F filings are quarterly, not daily; Finnhub has some institutional
ownership endpoints.

What we capture per (instrument, trade_date):
  - foreign_net_volume      (외국인 순매수, 주식 수, signed)
  - foreign_hold_ratio      (외국인 보유율, percent)
  - institutional_net_volume (기관 순매수, 주식 수, signed)
  - individual_net_volume   (개인 순매수, 주식 수, signed)
  - close_price             (참고용, 검증 + 디버깅)

Net volume signs follow the source: +가 매수, -가 매도. Conserve as-is
through the system so the UI can color-code without re-deriving.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class InvestorFlowData(BaseModel):
    """One trading day of investor-type net buy/sell for a symbol."""

    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    trade_date: date  # KST trading date
    foreign_net_volume: int            # signed shares
    foreign_hold_ratio: Decimal | None = None  # percent (e.g. 48.11)
    institutional_net_volume: int
    individual_net_volume: int
    close_price: int | None = None     # KRW for KR


class InvestorFlowAdapter(ABC):
    market_code: str
    source: str

    @abstractmethod
    async def fetch_recent(
        self, symbol: str, *, days: int = 60
    ) -> Sequence[InvestorFlowData]:
        """Return up to `days` trading-days of investor flow for `symbol`,
        newest first. Empty list on upstream failure (caller retries next
        tick — no exceptions for transient HTTP issues)."""
        ...

    async def aclose(self) -> None:
        return None
