from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DisclosureItem(BaseModel):
    """One disclosure row, shaped for the UI list. Body is never exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_id: str
    title: str
    filed_at: datetime
    report_type: str | None = None
    submitter: str | None = None
    raw_url: str | None = None


class DisclosureListResponse(BaseModel):
    instrument: str  # canonical id "{exchange}:{symbol}"
    count: int
    items: list[DisclosureItem]
