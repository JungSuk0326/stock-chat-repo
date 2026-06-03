"""KRX OpenAPI (https://openapi.krx.co.kr) client — 정식 REST API.

Distinct from `data.krx.co.kr` (which pykrx scrapes behind login). This is
the official Data Marketplace OpenAPI, key-based, JSON. Coverage today is
stock/index/ETF daily OHLCV + instrument basic info — **no investor-flow
breakdown**, so 사모/연기금 발굴은 여전히 pykrx 경로를 쓴다.

We use this for:
  - 일별 EOD 시세 sweep (KOSPI/KOSDAQ) — 1 call per (date × market)
    returns ALL listed stocks; cheaper for the watchlist daily cron than
    pykrx's per-symbol shape

We do NOT use it for:
  - 1-year backfill of a single symbol — OpenAPI is per-date, so 365 days
    = 730 calls (KOSPI+KOSDAQ); pykrx covers the whole range in 1 call
  - 종목 마스터 / 투자자별 매매동향 — out of catalog scope

Endpoint reference (verified against the public service list 2026-06):
  - POST /sto/stk_bydd_trd   (KOSPI 일별매매정보)
  - POST /sto/ksq_bydd_trd   (KOSDAQ 일별매매정보)
  - POST /sto/knx_bydd_trd   (KONEX 일별매매정보)
  - + 종목기본정보, 지수, ETF/ETN/ELW, 채권, 파생, ESG (미사용)

Request:
  GET https://data-dbg.krx.co.kr/svc/apis/sto/{endpoint}?AUTH_KEY=...&basDd=YYYYMMDD

Response (excerpt — KRX standard field names):
  {
    "OutBlock_1": [
      {
        "BAS_DD": "20260530",
        "ISU_CD": "005930",        # 단축코드 (6자리)
        "ISU_NM": "삼성전자",
        "MKT_NM": "KOSPI",
        "SECT_TP_NM": "STK",       # 보통주 등
        "TDD_OPNPRC": "55,000",    # 시가
        "TDD_HGPRC": "55,800",     # 고가
        "TDD_LWPRC": "54,500",     # 저가
        "TDD_CLSPRC": "55,500",    # 종가
        "CMPPREVDD_PRC": "+500",
        "FLUC_RT": "0.91",
        "ACC_TRDVOL": "12,345,678", # 거래량
        "ACC_TRDVAL": "..."         # 거래대금
        ...
      }, ...
    ]
  }

Holidays / weekends return an empty `OutBlock_1`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"

# Category + endpoint mapping for the markets we care about.
# Map KRX mktId codes to the OpenAPI endpoint path. STK/KSQ/KNX match the
# same vocabulary we use elsewhere (market_investor_flow.MARKETS) so
# higher-level callers can pass mktId straight through.
_MARKET_TO_ENDPOINT: dict[str, str] = {
    "STK": "sto/stk_bydd_trd",   # 유가증권 = KOSPI
    "KSQ": "sto/ksq_bydd_trd",   # 코스닥
    "KNX": "sto/knx_bydd_trd",   # 코넥스
}

# pykrx-openapi reference impl rate-limits 10/sec; we go conservative
# because we're a long-running background worker, not a throughput-bound
# notebook. Real cap is undocumented — this stays well under "normal use".
_DEFAULT_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class StockDailyRow:
    """One symbol's OHLCV for one trade date — what we keep from the
    response. Other KRX fields (시총, 상장주식수 등) are dropped here so the
    shape matches `app.services.market.base.PriceData`."""

    bas_dd: date
    symbol: str        # 6-digit ticker (단축코드)
    name: str
    market: str        # KOSPI / KOSDAQ / KONEX (raw KRX name)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class KrxOpenApiError(Exception):
    """Wraps HTTP / JSON / KRX-side errors so the daily sweep can log + skip
    rather than crash the whole worker."""


class KrxOpenApiClient:
    """Async client for KRX OpenAPI. Single shared instance per worker — the
    underlying httpx.AsyncClient is connection-pooled."""

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        concurrency: int = _DEFAULT_CONCURRENCY,
        timeout_s: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            headers={
                # KRX is fine with a generic UA, but identifying ourselves
                # gives them a contact path if usage looks abusive.
                "User-Agent": "stock-advisor (single-user; data-marketplace key)",
            },
        )
        self._sem = asyncio.Semaphore(concurrency)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- public methods ----------

    async def fetch_stock_daily(
        self, bas_dd: date, market: str
    ) -> Sequence[StockDailyRow]:
        """Return all symbols' OHLCV for one trade date in one market.

        `market` accepts STK / KSQ / KNX (KRX mktId codes). Empty result
        on weekends/holidays — the OpenAPI returns `OutBlock_1: []`."""
        endpoint = _MARKET_TO_ENDPOINT.get(market)
        if endpoint is None:
            raise KrxOpenApiError(
                f"Unsupported market for KRX OpenAPI: {market!r}. "
                f"Valid: {list(_MARKET_TO_ENDPOINT.keys())}"
            )

        params = {
            "AUTH_KEY": self._api_key,
            "basDd": bas_dd.strftime("%Y%m%d"),
        }
        url = f"{BASE_URL}/{endpoint}"

        async with self._sem:
            try:
                resp = await self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                raise KrxOpenApiError(
                    f"KRX OpenAPI network error ({endpoint}, {bas_dd}): {exc}"
                ) from exc

        if resp.status_code == 401:
            raise KrxOpenApiError("KRX OpenAPI 401 — invalid AUTH_KEY")
        if resp.status_code == 429:
            raise KrxOpenApiError(
                f"KRX OpenAPI 429 rate-limited at {endpoint} ({bas_dd})"
            )
        if resp.status_code >= 500:
            raise KrxOpenApiError(
                f"KRX OpenAPI {resp.status_code} server error at "
                f"{endpoint} ({bas_dd})"
            )
        if resp.status_code != 200:
            raise KrxOpenApiError(
                f"KRX OpenAPI {resp.status_code} at {endpoint} ({bas_dd}): "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise KrxOpenApiError(
                f"KRX OpenAPI non-JSON response ({endpoint}, {bas_dd}): "
                f"{resp.text[:200]}"
            ) from exc

        block: list[dict[str, Any]] = payload.get("OutBlock_1") or []
        if not block:
            return []

        out: list[StockDailyRow] = []
        for row in block:
            parsed = _parse_row(row)
            if parsed is not None:
                out.append(parsed)
        return out


# ---------- parsing ----------


def _to_decimal(s: Any) -> Decimal | None:
    """KRX returns prices as strings with commas: '55,500'. Empty / '-' → None."""
    if s is None:
        return None
    cleaned = str(s).replace(",", "").strip()
    if not cleaned or cleaned in ("-", "—"):
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _to_int(s: Any) -> int | None:
    d = _to_decimal(s)
    if d is None:
        return None
    try:
        return int(d)
    except (ValueError, OverflowError):
        return None


def _to_date(s: Any) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    # Accept both YYYYMMDD and YYYY/MM/DD.
    s = s.replace("/", "").replace("-", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _parse_row(row: dict[str, Any]) -> StockDailyRow | None:
    bas_dd = _to_date(row.get("BAS_DD"))
    symbol = (row.get("ISU_CD") or "").strip()
    if bas_dd is None or not symbol:
        return None

    open_ = _to_decimal(row.get("TDD_OPNPRC"))
    high = _to_decimal(row.get("TDD_HGPRC"))
    low = _to_decimal(row.get("TDD_LWPRC"))
    close = _to_decimal(row.get("TDD_CLSPRC"))
    volume = _to_int(row.get("ACC_TRDVOL"))
    if any(v is None for v in (open_, high, low, close, volume)):
        # KRX can emit zero-trade days with empty price strings — those
        # don't make a meaningful bar.
        return None

    return StockDailyRow(
        bas_dd=bas_dd,
        symbol=symbol,
        name=(row.get("ISU_NM") or "").strip(),
        market=(row.get("MKT_NM") or "").strip(),
        open=open_,             # type: ignore[arg-type]
        high=high,              # type: ignore[arg-type]
        low=low,                # type: ignore[arg-type]
        close=close,            # type: ignore[arg-type]
        volume=volume,          # type: ignore[arg-type]
    )


def utc_midnight_of(d: date) -> datetime:
    """Conversion helper: the canonical `prices.time` for a daily bar
    is midnight UTC of the trading date (matches how pykrx-backed
    `KrMarketAdapter.fetch_eod_prices` already records them)."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
