"""Naver Finance polling realtime feed — KRX + NXT in one call.

Endpoint (discovered by inspecting finance.naver.com/item/main.naver?code=):

  GET https://polling.finance.naver.com/api/realtime
      ?query=SERVICE_ITEM:{symbol}

Response (excerpt, fields relevant to us):
  {
    "result": {
      "pollingInterval": 70000,
      "areas": [{
        "datas": [{
          "cd": "005930",
          "nv": 351500,              # KRX 현재가 (정수, 원)
          "ov": 359000,              # KRX 시가
          "hv": 366000, "lv": 348000,
          "aq": 34290059,            # KRX 누적거래량
          "aa": 12172611000000,      # KRX 누적거래대금
          "nxtOverMarketPriceInfo": {  # ★ NXT 별도 객체. null이면 NXT 데이터 없음.
            "tradingSessionType": "AFTER_MARKET" | "PRE_MARKET" | "MAIN_MARKET",
            "overMarketStatus": "OPEN" | "CLOSE",
            "overPrice": "342,000",            # NXT 현재가 (스트링, 콤마)
            "openPrice": "359,000",
            "highPrice": "366,500", "lowPrice": "338,500",
            "accumulatedTradingVolume": "26,339,275",
            "localTradedAt": "2026-06-04T20:00:00.000000+09:00"
          }
        }]
      }]
    }
  }

NXT 비중이 종목당 ~30-50%까지 나오는 케이스가 있어 무시 불가. 이 어댑터로
폴러가 한 번 호출에 KRX/NXT 둘 다 받아 별도 RealtimePrice 두 개로 발행한다.

Naver suggests pollingInterval=70000ms (70s). We poll faster (5s default
from the worker config) because the worker uses the same Naver Mobile API
at 2s without issues; if throttling shows up, drop to 70s.

비공식 API. 본인용 한정.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import structlog

from app.services.market.base import RealtimePrice

log = structlog.get_logger()


_POLLING_URL = "https://polling.finance.naver.com/api/realtime"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (stock-advisor private)",
    "Referer": "https://finance.naver.com/",
}


def _parse_won(s: Any) -> Decimal | None:
    if s is None:
        return None
    if isinstance(s, (int, float, Decimal)):
        try:
            return Decimal(str(s))
        except Exception:  # noqa: BLE001
            return None
    cleaned = str(s).replace(",", "").strip()
    if not cleaned or cleaned in ("-", "—"):
        return None
    try:
        return Decimal(cleaned)
    except Exception:  # noqa: BLE001
        return None


def _parse_volume(s: Any) -> int | None:
    d = _parse_won(s)
    if d is None:
        return None
    try:
        return int(d)
    except (ValueError, OverflowError):
        return None


class NaverPollingAdapter:
    """Single-call adapter that yields both venues' realtime snapshots.

    Not a full `MarketAdapter` — this only handles the realtime feed.
    Historical / instrument-master concerns stay with `KrMarketAdapter`.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers=_HEADERS,
        )

    async def fetch_realtime_prices(
        self, symbol: str
    ) -> list[RealtimePrice]:
        """Return a list of snapshots, one per venue that has data right now.

        Empty list on upstream failure (caller retries next tick). NXT entry
        is included only when the response carries a non-null
        `nxtOverMarketPriceInfo` (Naver omits it for symbols not listed on
        NXT or before NXT data is available for the session)."""
        params = {"query": f"SERVICE_ITEM:{symbol}"}
        try:
            resp = await self._http.get(_POLLING_URL, params=params)
            resp.raise_for_status()
            # Naver returns text/plain;charset=EUC-KR but the actual encoding
            # is CP949 (Korean superset including 사ㄴ/년 etc). httpx's default
            # `.json()` strict-decodes as EUC-KR and dies on some glyphs.
            # Decode bytes ourselves with CP949 (errors=replace covers any
            # rare unencodable byte without aborting the tick).
            payload = json.loads(resp.content.decode("cp949", errors="replace"))
        except httpx.HTTPError as exc:
            log.warning("naver.polling.http_error", symbol=symbol, error=str(exc))
            return []
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("naver.polling.parse_error", symbol=symbol, error=str(exc))
            return []

        result = (payload or {}).get("result") or {}
        areas = result.get("areas") or []
        if not areas:
            return []
        datas = areas[0].get("datas") or []
        if not datas:
            return []
        row = datas[0]

        # Request-time UTC. Naver doesn't return a precise tick timestamp for
        # the KRX side of this endpoint — same compromise as the mobile API.
        ts_utc = datetime.now(tz=timezone.utc)

        out: list[RealtimePrice] = []

        # ---- KRX leg ----
        krx_close = _parse_won(row.get("nv"))
        krx_vol = _parse_volume(row.get("aq"))
        if krx_close is not None and krx_vol is not None:
            out.append(
                RealtimePrice(
                    ts=ts_utc,
                    venue="KRX",
                    close=krx_close,
                    open=_parse_won(row.get("ov")),
                    high=_parse_won(row.get("hv")),
                    low=_parse_won(row.get("lv")),
                    volume_cum=krx_vol,
                )
            )

        # ---- NXT leg (optional) ----
        nxt = row.get("nxtOverMarketPriceInfo")
        if isinstance(nxt, dict):
            nxt_close = _parse_won(nxt.get("overPrice"))
            nxt_vol = _parse_volume(nxt.get("accumulatedTradingVolume"))
            # If NXT shows zero or null close, this symbol just isn't trading
            # on NXT right now; skip rather than emit a fake bar.
            if nxt_close is not None and nxt_close > 0 and nxt_vol is not None:
                out.append(
                    RealtimePrice(
                        ts=ts_utc,
                        venue="NXT",
                        close=nxt_close,
                        open=_parse_won(nxt.get("openPrice")),
                        high=_parse_won(nxt.get("highPrice")),
                        low=_parse_won(nxt.get("lowPrice")),
                        volume_cum=nxt_vol,
                    )
                )

        return out

    async def aclose(self) -> None:
        await self._http.aclose()
