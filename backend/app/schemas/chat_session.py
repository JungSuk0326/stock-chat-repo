from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatSessionSummary(BaseModel):
    """One session row for the dropdown / sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    instrument: str  # canonical "{exchange}:{symbol}"
    title: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime


class ChatSessionListResponse(BaseModel):
    count: int
    items: list[ChatSessionSummary]


class ChatSessionCreateRequest(BaseModel):
    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    title: str | None = Field(default=None, max_length=128)


class ChatMessageRecord(BaseModel):
    """One stored message for session replay."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime


class ChatSessionDetailResponse(BaseModel):
    session: ChatSessionSummary
    messages: list[ChatMessageRecord]
