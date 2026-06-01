"""News adapter abstraction.

Phase 1 implements `NaverNewsAdapter` for KR (Naver Finance mobile API).
Phase 2 will add a US adapter (Finnhub / Marketaux / RSS). Higher-level
callers (worker, assemble_context, /news endpoint) depend on this
interface only.

Body text is NEVER carried. Headlines + metadata only — sidesteps
copyright + Naver Mobile API's gray-area scraping policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NewsItemData(BaseModel):
    """Adapter-level news record. Decoupled from the ORM model so the
    same shape works across markets."""

    source: str = Field(..., max_length=32)       # "naver", "rss:hankyung", ...
    source_id: str = Field(..., max_length=64)    # stable per-article id (UNIQUE)
    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    title: str = Field(..., max_length=512)
    published_at: datetime                        # UTC
    url: str = Field(..., max_length=512)
    publisher: str | None = Field(default=None, max_length=128)  # 언론사 (e.g. "중앙일보")


class NewsAdapter(ABC):
    """Per-market news source adapter."""

    #: Marker for filter logic — Phase 1 KR only.
    market_code: Literal["KR", "US", "JP"]

    @abstractmethod
    async def fetch_news(
        self,
        symbol: str,
        *,
        limit: int = 50,
    ) -> Sequence[NewsItemData]:
        """Return latest `limit` news items for `symbol`, newest first.

        The polling and backfill workers both use this. Adapters should
        return the freshest items they can get in one call; pagination is
        an adapter-internal detail (caller doesn't request specific pages).
        """
        ...

    async def aclose(self) -> None:
        """Optional cleanup. Default no-op."""
        return None
