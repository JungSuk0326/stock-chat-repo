from datetime import date as date_type, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class InvestorFlowItem(BaseModel):
    """One trading-day row as exposed to the UI."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trade_date: date_type
    foreign_net_volume: int
    foreign_hold_ratio: Decimal | None
    institutional_net_volume: int
    individual_net_volume: int
    close_price: int | None
    source: str
    created_at: datetime


class InvestorFlowListResponse(BaseModel):
    instrument: str
    count: int
    items: list[InvestorFlowItem]
