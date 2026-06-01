"""KR news adapter — Naver Finance Mobile.

Response shape (the actual JSON we get from the endpoint):

    [
      {
        "total": 1,
        "items": [
          {
            "id": "0250003527347",           # office_id + article_id, stable
            "officeId": "025",
            "articleId": "0003527347",
            "officeName": "중앙일보",         # publisher
            "datetime": "202606020020",       # YYYYMMDDHHMM, KST
            "title": "...",
            "body": "...",                   # ← discarded (copyright/policy)
            "mobileNewsUrl": "https://n.news.naver.com/mnews/article/025/...",
            ...
          }
        ]
      },
      { "total": 1, "items": [ ... ] },
      ...
    ]

Each top-level array entry is a "news cluster" of related articles; we
flatten by iterating items inside each. The cluster grouping doesn't
matter to us — we treat each item as an independent piece of news.

The endpoint requires a browser-ish User-Agent. Same caveat as the price
adapter: unofficial, single-user fine, do not redistribute.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog

from app.services.news.base import NewsAdapter, NewsItemData

log = structlog.get_logger()

_NAVER_NEWS_URL = "https://m.stock.naver.com/api/news/stock/{symbol}"
_KST = ZoneInfo("Asia/Seoul")
_UTC = ZoneInfo("UTC")


class NaverNewsAdapter(NewsAdapter):
    market_code = "KR"
    source = "naver"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": "Mozilla/5.0 (stock-advisor private)"},
        )

    async def fetch_news(
        self,
        symbol: str,
        *,
        limit: int = 50,
    ) -> Sequence[NewsItemData]:
        # `pageSize` here is the number of clusters Naver returns, not
        # articles — but each cluster typically has 1 item, so it works as
        # an effective article cap.
        params = {"pageSize": limit, "page": 1}
        try:
            resp = await self._http.get(
                _NAVER_NEWS_URL.format(symbol=symbol),
                params=params,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("naver.news.http_failed", symbol=symbol, error=str(exc))
            return []

        try:
            data = resp.json()
        except ValueError as exc:
            log.warning("naver.news.parse_failed", symbol=symbol, error=str(exc))
            return []

        if not isinstance(data, list):
            return []

        out: list[NewsItemData] = []
        for cluster in data:
            for item in (cluster or {}).get("items", []) or []:
                parsed = _parse_item(item, symbol)
                if parsed is not None:
                    out.append(parsed)
        return out


def _parse_item(item: dict[str, Any], symbol: str) -> NewsItemData | None:
    article_id = (item.get("id") or "").strip()
    if not article_id:
        return None
    title = (item.get("title") or "").strip()
    if not title:
        return None
    dt_str = (item.get("datetime") or "").strip()
    published_at = _parse_naver_datetime(dt_str)
    if published_at is None:
        return None
    url = (item.get("mobileNewsUrl") or "").strip()
    if not url:
        # fall back to a deterministic URL — better than dropping the row
        url = (
            f"https://n.news.naver.com/mnews/article/"
            f"{item.get('officeId', '')}/{item.get('articleId', '')}"
        )
    publisher = (item.get("officeName") or "").strip() or None
    return NewsItemData(
        source="naver",
        source_id=article_id,
        exchange="KR",
        symbol=symbol,
        title=title[:512],
        published_at=published_at,
        url=url[:512],
        publisher=publisher[:128] if publisher else None,
    )


def _parse_naver_datetime(s: str) -> datetime | None:
    """YYYYMMDDHHMM in KST → UTC datetime. Returns None on malformed input."""
    if not s or len(s) != 12 or not s.isdigit():
        return None
    try:
        dt_kst = datetime(
            int(s[0:4]),
            int(s[4:6]),
            int(s[6:8]),
            int(s[8:10]),
            int(s[10:12]),
            tzinfo=_KST,
        )
    except ValueError:
        return None
    return dt_kst.astimezone(_UTC)
