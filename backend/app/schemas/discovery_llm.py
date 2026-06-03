"""Schemas for the natural-language discovery endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiscoveryLlmRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    # Optional model override — same shape as POST /chat. Defaults to
    # settings.LLM_DEFAULT_PROVIDER / settings.LLM_DEFAULT_MODEL.
    provider: str | None = None
    model: str | None = None


class DiscoveryCandidateOut(BaseModel):
    """One ranked symbol as exposed to the frontend cards."""

    exchange: str
    symbol: str
    name: str
    metric_label: str          # e.g. "사모 순매수 (30일 누계)"
    metric_value: int          # signed KRW


class DiscoveryLlmResponse(BaseModel):
    answer: str
    candidates: list[DiscoveryCandidateOut]
    # Surfacing the tool calls helps the UI render "사용된 조건" badges and
    # also lets us debug LLM misroutes (e.g. wrong investor_type).
    tools_called: list[dict] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
