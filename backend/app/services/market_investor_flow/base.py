"""Adapter contract for market-wide investor-type trading flows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class MarketInvestorFlowRow:
    """One row = one (trade_date, market, investor_type) net-buy figure.

    `net_value` is signed KRW (or signed currency unit for non-KR adapters
    eventually): positive = net buy by that investor type, negative = net
    sell. Buy/sell legs are optional — sources that only emit net leave
    them None.
    """

    trade_date: date
    market: str            # canonical: STK / KSQ / ALL (KRX) — see model
    investor_type: str     # canonical key from MarketInvestorFlow.INVESTOR_TYPES
    net_value: int
    buy_value: int | None = None
    sell_value: int | None = None
    source: str = "krx"


class MarketInvestorFlowAdapter(ABC):
    market_code: str
    source: str

    @abstractmethod
    async def fetch_daily(
        self, start: date, end: date, *, market: str
    ) -> Sequence[MarketInvestorFlowRow]:
        """Return rows for [start, end] in `market`, one per
        (trade_date, market, investor_type). Empty list on upstream
        failure — caller treats this as transient (no exceptions for
        normal "no data yet today" cases)."""
        ...

    async def aclose(self) -> None:
        return None
