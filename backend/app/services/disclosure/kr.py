"""DART OpenAPI adapter — Korean regulatory disclosures.

Two endpoints in play:
  - corpCode.xml  (ZIP-packed): full corp_code ↔ stock_code mapping. Refreshed
                  daily by a worker job (R11).
  - list.json:    per-corp disclosure list within a date range. Used for the
                  per-minute polling worker AND the 6-month backfill on
                  new watchlist additions.

DART rate limit: 20,000 calls / API key / day (no per-second limit, but
abusive bursts may be blocked). Per-symbol minute polling with a watchlist
of ~10 stays under 14.4k/day. The big consumer is backfill on watchlist
joins — one-shot, then idempotent.

Reference timestamps:
  - rcept_dt is a date-only ("YYYYMMDD") in KST. We pin filed_at to 00:00
    KST → UTC so ORDER BY filed_at gives a sane (if coarse) timeline.
    Same-day filings tie-break by rcept_no (DART's monotonically-increasing
    receipt number).
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import Sequence
from datetime import date, datetime, time
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx
import structlog

from app.services.disclosure.base import (
    CorpCodeEntry,
    DisclosureAdapter,
    DisclosureData,
)

log = structlog.get_logger()

_DART_BASE = "https://opendart.fss.or.kr/api"
_CORPCODE_URL = f"{_DART_BASE}/corpCode.xml"
_LIST_URL = f"{_DART_BASE}/list.json"
_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"

_KST = ZoneInfo("Asia/Seoul")
_UTC = ZoneInfo("UTC")

_PAGE_SIZE = 100  # DART list.json max


class DartApiError(RuntimeError):
    """Raised on non-recoverable DART responses (auth, malformed, ...).

    Transient errors (rate limit "020" / no data "013") are NOT raised — the
    caller-facing methods return an empty sequence so the worker can move on.
    """

    def __init__(self, status: str, message: str) -> None:
        super().__init__(f"DART {status}: {message}")
        self.status = status
        self.message = message


def _kst_date_to_utc(d: date) -> datetime:
    """00:00 KST on `d` expressed in UTC."""
    return datetime.combine(d, time.min, tzinfo=_KST).astimezone(_UTC)


def _parse_rcept_dt(s: str) -> date | None:
    """DART rcept_dt is 'YYYYMMDD'."""
    if not s or len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _viewer_url(rcept_no: str) -> str:
    return f"{_VIEWER_URL}?rcpNo={rcept_no}"


class DartAdapter(DisclosureAdapter):
    """Async DART OpenAPI client.

    A single long-lived `httpx.AsyncClient` is held internally — `aclose()`
    is honored by the worker on shutdown.
    """

    source = "dart"

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            log.warning("dart.no_api_key")
        self._api_key = api_key
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": "stock-advisor private"},
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- corp_code ----------

    async def fetch_corp_codes(self) -> Sequence[CorpCodeEntry]:
        if not self.configured:
            log.warning("dart.fetch_corp_codes.skipped_no_key")
            return []

        log.info("dart.fetch_corp_codes.started")
        resp = await self._http.get(
            _CORPCODE_URL,
            params={"crtfc_key": self._api_key},
        )
        resp.raise_for_status()

        # The body is either a ZIP (success) or a small JSON error blob.
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype.lower():
            data = resp.json()
            raise DartApiError(
                status=str(data.get("status", "?")),
                message=str(data.get("message", "unknown")),
            )

        # ZIP parsing is CPU-bound but tiny (~few MB) — to_thread to avoid
        # blocking the event loop.
        entries = await asyncio.to_thread(_parse_corp_code_zip, resp.content)
        log.info("dart.fetch_corp_codes.done", total=len(entries))
        return entries

    # ---------- disclosure list ----------

    async def fetch_recent_disclosures(
        self,
        source_corp_id: str,
        start: date,
        end: date,
    ) -> Sequence[DisclosureData]:
        if not self.configured:
            return []

        out: list[DisclosureData] = []
        page_no = 1
        while True:
            params = {
                "crtfc_key": self._api_key,
                "corp_code": source_corp_id,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_no": page_no,
                "page_count": _PAGE_SIZE,
            }
            try:
                resp = await self._http.get(_LIST_URL, params=params)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning(
                    "dart.list.http_failed",
                    corp=source_corp_id,
                    page=page_no,
                    error=str(exc),
                )
                return out  # return what we got so far; caller retries next tick

            data = resp.json()
            status = str(data.get("status", "?"))

            if status == "013":
                # "조회된 데이타가 없습니다" — normal empty result
                return out
            if status != "000":
                # 020 too many requests, 100 invalid arg, 800 system busy, etc.
                # Treat as transient unless 010* (auth) which is fatal config.
                if status.startswith("010"):
                    raise DartApiError(status, str(data.get("message", "")))
                log.warning(
                    "dart.list.api_status",
                    corp=source_corp_id,
                    status=status,
                    message=data.get("message"),
                )
                return out

            for row in data.get("list", []):
                d = _parse_rcept_dt(str(row.get("rcept_dt", "")))
                if d is None:
                    continue
                stock_code = str(row.get("stock_code", "")).strip()
                if not stock_code or not stock_code.isdigit():
                    # Non-listed / preferred share / placeholder — skip.
                    continue
                rcept_no = str(row.get("rcept_no", "")).strip()
                if not rcept_no:
                    continue
                out.append(
                    DisclosureData(
                        source="dart",
                        source_id=rcept_no,
                        exchange="KR",
                        symbol=stock_code,
                        title=str(row.get("report_nm", "")).strip()[:512],
                        filed_at=_kst_date_to_utc(d),
                        report_type=(str(row.get("corp_cls", "")).strip() or None),
                        submitter=(str(row.get("flr_nm", "")).strip() or None),
                        raw_url=_viewer_url(rcept_no),
                    )
                )

            total_page = int(data.get("total_page", 1) or 1)
            if page_no >= total_page:
                return out
            page_no += 1


def _parse_corp_code_zip(blob: bytes) -> list[CorpCodeEntry]:
    """Parse the ZIP body returned by DART's corpCode.xml endpoint.

    The archive contains a single file `CORPCODE.xml` whose root has many
    <list> children. Each <list> has corp_code / corp_name / stock_code /
    modify_date. stock_code is blank for non-listed / delisted firms.
    """
    out: list[CorpCodeEntry] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        # The archive name is "CORPCODE.xml" but we don't hard-code that.
        names = zf.namelist()
        if not names:
            return out
        with zf.open(names[0]) as f:
            tree = ET.parse(f)
    root = tree.getroot()
    for node in root.findall("list"):
        corp_code = (node.findtext("corp_code") or "").strip()
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_name = (node.findtext("corp_name") or "").strip() or None

        if not corp_code:
            continue
        # We only care about listed firms with a 6-digit stock code; the
        # mapping table for non-listed companies would just bloat the DB.
        if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
            continue
        out.append(
            CorpCodeEntry(
                source="dart",
                source_corp_id=corp_code,
                exchange="KR",
                symbol=stock_code,
                name=corp_name,
            )
        )
    return out
