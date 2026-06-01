from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NewsItemDTO(BaseModel):
    """One headline as exposed to the UI. Body is never carried (never
    stored either — see app/models/news.py)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_id: str
    title: str
    published_at: datetime
    url: str
    publisher: str | None


class NewsListResponse(BaseModel):
    instrument: str  # canonical "{exchange}:{symbol}"
    count: int
    items: list[NewsItemDTO]
