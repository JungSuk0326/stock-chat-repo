"""Pydantic schemas for the market-wide investor-flow endpoints."""

from datetime import date as date_type

from pydantic import BaseModel, ConfigDict


class MarketInvestorFlowItem(BaseModel):
    """One (trade_date, market, investor_type) net-buy row.

    `investor_type` is the canonical key (e.g. `private_fund`); the
    Korean label is in `investor_label_ko` for direct UI rendering
    without a client-side lookup table.
    """

    model_config = ConfigDict(from_attributes=True)

    trade_date: date_type
    market: str               # STK / KSQ / ALL
    investor_type: str        # canonical key
    investor_label_ko: str    # 사모, 연기금, 외국인 …
    net_value: int            # signed KRW
    buy_value: int | None
    sell_value: int | None


class MarketInvestorFlowListResponse(BaseModel):
    market: str
    investor_types: list[str] | None  # None = all
    count: int
    items: list[MarketInvestorFlowItem]
