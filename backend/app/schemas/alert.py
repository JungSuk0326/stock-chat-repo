from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ConditionType = Literal[
    "price_above",
    "price_below",
    "pct_change_above",
    "pct_change_below",
]


class AlertRuleSummary(BaseModel):
    """Persisted rule as exposed to the UI."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    instrument: str  # canonical "{exchange}:{symbol}"
    name: str | None
    condition_type: ConditionType
    threshold: Decimal
    enabled: bool
    cooldown_minutes: int
    market_hours_only: bool
    last_triggered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertRuleListResponse(BaseModel):
    count: int
    items: list[AlertRuleSummary]


class AlertRuleCreateRequest(BaseModel):
    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    name: str | None = Field(default=None, max_length=128)
    condition_type: ConditionType
    threshold: Decimal
    cooldown_minutes: int = Field(default=60, ge=1, le=10_080)  # ≤ 1 week
    market_hours_only: bool = False


class AlertRuleUpdateRequest(BaseModel):
    enabled: bool


class AlertEventRecord(BaseModel):
    """Recent fire history for a rule (for debugging the UI)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    fired_at: datetime
    triggered_value: Decimal
    channel: str
    delivery_status: str
    error: str | None
