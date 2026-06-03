from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ----- Screener -----


class ScreenerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    universe: dict[str, Any] = Field(default_factory=dict)
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True


class ScreenerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    universe: dict[str, Any] | None = None
    criteria: list[dict[str, Any]] | None = None
    enabled: bool | None = None


class ScreenerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    universe: dict[str, Any]
    criteria: list[dict[str, Any]]
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    candidate_count: int | None = None  # populated by list/detail handlers


class ScreenerListResponse(BaseModel):
    count: int
    items: list[ScreenerSummary]


class ScreenerRunResponse(BaseModel):
    screener_id: int
    new_candidates: int


# ----- Candidate -----


class CandidateSummary(BaseModel):
    id: int
    instrument: str  # "EX:SYM"
    instrument_name: str | None
    source: str
    score: Decimal | None
    reason: str | None
    status: str
    discovered_at: datetime
    snoozed_until: datetime | None
    updated_at: datetime


class CandidateListResponse(BaseModel):
    count: int
    items: list[CandidateSummary]


class CandidateSnoozeRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
