"""LLM-callable discovery tools.

Separate from `app/llm/tools.py` (which holds alert CRUD) because:
  - discovery is pure read; no confirmation card flow
  - discovery executors return a structured `DiscoveryToolResult` so the
    endpoint can surface both an LLM-facing summary string AND a
    typed candidate list to the frontend
  - keeping the two registries separate prevents the discovery endpoint
    from accidentally exposing alert-write tools to the model

Today only one tool — `discover_by_investor_flow` — wrapping pykrx's
`get_market_net_purchases_of_equities`. The result is Redis-cached
(EOD data doesn't change post-close so a multi-hour TTL is fine).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import structlog
from pandas import DataFrame
from pykrx import stock
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import ToolDef
from app.models import Instrument
from app.models.market_investor_flow import (
    INVESTOR_TYPE_LABELS_KO,
    INVESTOR_TYPES,
)

log = structlog.get_logger()


# Canonical investor key → pykrx Korean label. pykrx's
# get_market_net_purchases_of_equities accepts these exact strings.
# `institutional` maps to pykrx's "기관합계" — the pre-aggregated sum.
_INVESTOR_KEY_TO_PYKRX: dict[str, str] = {
    "financial_investment": "금융투자",
    "insurance": "보험",
    "investment_trust": "투신",
    "private_fund": "사모",
    "bank": "은행",
    "other_finance": "기타금융",
    "pension": "연기금",
    "other_corp": "기타법인",
    "individual": "개인",
    "foreign": "외국인",
    "other_foreign": "기타외국인",
    "institutional": "기관합계",
}

# Market filter for the LLM tool — only the ones pykrx accepts. KONEX
# omitted on purpose (illiquid, off-scope for personal use).
_DISCOVERY_MARKETS = ("KOSPI", "KOSDAQ", "ALL")

# Redis cache TTL for tool calls. EOD market data stops changing after
# 16:00 KST so 6 hours is generous. Set short enough that adding a hand-
# fix to the underlying mapping doesn't get masked for a full day.
_CACHE_TTL_SECONDS = 6 * 3600

# Hard limits to keep pykrx + LLM bills sane. Per-call top_n cap is
# generous because pykrx returns the whole ranking anyway — the trim is
# just for the payload sent to the LLM.
_MAX_PERIOD_DAYS = 90
_MAX_TOP_N = 20
_DEFAULT_TOP_N = 10


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """One ranked symbol the tool surfaces."""

    exchange: str
    symbol: str
    name: str
    metric_label: str
    metric_value: int  # signed KRW (net purchase value)


@dataclass
class DiscoveryToolResult:
    """Output of a discovery tool invocation.

    `text` goes back to the LLM (it can quote it naturally in prose).
    `candidates` is consumed by the frontend to render result cards
    with "관심종목 추가" buttons.
    """

    text: str
    candidates: list[DiscoveryCandidate] = field(default_factory=list)


# (args, db, redis) → result. db is for instrument id ↔ ticker mapping;
# redis for caching the pykrx call.
DiscoveryToolExecutor = Callable[
    [dict[str, Any], AsyncSession, Redis], Awaitable[DiscoveryToolResult]
]


@dataclass
class DiscoveryToolSpec:
    definition: ToolDef
    executor: DiscoveryToolExecutor


# ---------- discover_by_investor_flow ----------


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _cache_key(market: str, investor: str, fromdate: str, todate: str) -> str:
    raw = f"{market}|{investor}|{fromdate}|{todate}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"discover:investor_flow:{h}"


async def _fetch_ranking_cached(
    redis: Redis,
    *,
    market: str,
    investor_label_ko: str,
    fromdate: str,
    todate: str,
) -> list[dict[str, Any]]:
    """Fetch the pykrx net-purchase ranking, with Redis caching.

    Returns a list of raw row dicts:
        [{"ticker": "005930", "name": "삼성전자", "net_value": ..., "net_volume": ...}, ...]
    sorted by net_value descending. Empty list on upstream failure.
    """
    key = _cache_key(market, investor_label_ko, fromdate, todate)
    cached = await redis.get(key)
    if cached is not None:
        try:
            return json.loads(
                cached if isinstance(cached, str) else cached.decode()
            )
        except Exception:  # noqa: BLE001
            log.warning("discovery.cache.parse_failed", key=key)

    try:
        df: DataFrame = await asyncio.to_thread(
            stock.get_market_net_purchases_of_equities,
            fromdate,
            todate,
            market,
            investor_label_ko,
        )
    except Exception as exc:  # noqa: BLE001 — pykrx wraps many errors
        log.warning(
            "discovery.pykrx.fetch_failed",
            market=market,
            investor=investor_label_ko,
            error=str(exc),
        )
        return []

    if df is None or df.empty:
        return []

    rows: list[dict[str, Any]] = []
    # pykrx columns: 종목명, 매도거래량, 매수거래량, 순매수거래량,
    # 매도거래대금, 매수거래대금, 순매수거래대금. Index = ticker.
    for ticker, row in df.iterrows():
        name = str(row.get("종목명") or "").strip()
        net_value_raw = row.get("순매수거래대금")
        net_volume_raw = row.get("순매수거래량")
        try:
            net_value = int(net_value_raw) if net_value_raw is not None else 0
        except (TypeError, ValueError):
            net_value = 0
        try:
            net_volume = int(net_volume_raw) if net_volume_raw is not None else 0
        except (TypeError, ValueError):
            net_volume = 0
        rows.append(
            {
                "ticker": str(ticker),
                "name": name,
                "net_value": net_value,
                "net_volume": net_volume,
            }
        )

    # Cache only on success. Failure stays uncached so a retry can pick
    # up new credentials / a fixed upstream.
    await redis.set(key, json.dumps(rows), ex=_CACHE_TTL_SECONDS)
    return rows


def _format_won(v: int) -> str:
    """Compact KRW formatter for LLM prose. 8자리 이상이면 억 단위, 그 외 원."""
    if abs(v) >= 100_000_000:  # 1억
        return f"{v / 100_000_000:+.1f}억원"
    if abs(v) >= 10_000:
        return f"{v / 10_000:+.1f}만원"
    return f"{v:+,d}원"


async def _exec_discover_by_investor_flow(
    args: dict[str, Any], db: AsyncSession, redis: Redis
) -> DiscoveryToolResult:
    investor_key = (args.get("investor_type") or "").strip()
    market = (args.get("market") or "ALL").strip().upper()
    period_days_raw = args.get("period_days") or 30
    top_n_raw = args.get("top_n") or _DEFAULT_TOP_N
    direction = (args.get("direction") or "buy").strip().lower()

    # ---- input validation ----
    if investor_key not in _INVESTOR_KEY_TO_PYKRX:
        return DiscoveryToolResult(
            text=(
                f"investor_type='{investor_key}'는 지원하지 않습니다. "
                f"가능한 값: {list(_INVESTOR_KEY_TO_PYKRX.keys())}"
            )
        )
    if market not in _DISCOVERY_MARKETS:
        return DiscoveryToolResult(
            text=f"market='{market}'는 지원하지 않습니다. 가능한 값: {list(_DISCOVERY_MARKETS)}"
        )
    try:
        period_days = max(1, min(int(period_days_raw), _MAX_PERIOD_DAYS))
    except (TypeError, ValueError):
        period_days = 30
    try:
        top_n = max(1, min(int(top_n_raw), _MAX_TOP_N))
    except (TypeError, ValueError):
        top_n = _DEFAULT_TOP_N
    if direction not in ("buy", "sell"):
        direction = "buy"

    investor_label_ko = _INVESTOR_KEY_TO_PYKRX[investor_key]
    end = date.today()
    start = end - timedelta(days=period_days)
    fromdate, todate = _yyyymmdd(start), _yyyymmdd(end)

    rows = await _fetch_ranking_cached(
        redis,
        market=market,
        investor_label_ko=investor_label_ko,
        fromdate=fromdate,
        todate=todate,
    )
    if not rows:
        return DiscoveryToolResult(
            text=(
                f"데이터가 비어 있습니다 ({market} / {investor_label_ko} / "
                f"{fromdate}~{todate}). KRX 로그인 또는 영업일 누락 여부를 확인하세요."
            )
        )

    # buy: 순매수거래대금 내림차순 (가장 많이 산 종목 위로)
    # sell: 오름차순 (= 가장 많이 판 종목 위로)
    rows.sort(key=lambda r: r["net_value"], reverse=(direction == "buy"))
    top = rows[:top_n]

    # Map ticker → Instrument (lookup name/exchange canonical form). KRX
    # tickers in pykrx are 6-digit strings — our `instruments.symbol`
    # uses the same shape, so a direct IN-list lookup is fine.
    tickers = [r["ticker"] for r in top]
    inst_map: dict[str, Instrument] = {}
    if tickers:
        stmt = select(Instrument).where(
            Instrument.exchange == "KR",
            Instrument.symbol.in_(tickers),
        )
        for inst in (await db.execute(stmt)).scalars().all():
            inst_map[inst.symbol] = inst

    metric_label_ko = INVESTOR_TYPE_LABELS_KO.get(investor_key, investor_key)
    direction_ko = "순매수" if direction == "buy" else "순매도"

    candidates: list[DiscoveryCandidate] = []
    lines: list[str] = []
    for rank, r in enumerate(top, start=1):
        ticker = r["ticker"]
        inst = inst_map.get(ticker)
        # Prefer canonical instrument name if we have it; else pykrx's
        # name as a fallback (still useful for the LLM prose).
        display_name = (inst.name if inst else None) or r["name"] or ticker
        net_value = r["net_value"]

        candidates.append(
            DiscoveryCandidate(
                exchange="KR",
                symbol=ticker,
                name=display_name,
                metric_label=f"{metric_label_ko} {direction_ko} ({period_days}일 누계)",
                metric_value=net_value,
            )
        )
        lines.append(
            f"{rank}. KR:{ticker} {display_name} — {_format_won(net_value)}"
        )

    summary = (
        f"# {metric_label_ko} {direction_ko} 상위 (market={market}, "
        f"{period_days}일 누계, {start}~{end})\n\n" + "\n".join(lines)
    )
    return DiscoveryToolResult(text=summary, candidates=candidates)


# ---------- registry ----------


DISCOVERY_TOOLS: dict[str, DiscoveryToolSpec] = {
    "discover_by_investor_flow": DiscoveryToolSpec(
        definition=ToolDef(
            name="discover_by_investor_flow",
            description=(
                "특정 투자자 유형(예: 사모펀드, 연기금, 외국인)이 최근 N일간 "
                "가장 많이 순매수(또는 순매도)한 종목을 KRX 데이터에서 조회한다. "
                "한국 주식 KOSPI/KOSDAQ만 지원. period_days는 기본 30일, "
                "top_n은 기본 10. investor_type은 canonical 키를 사용한다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "investor_type": {
                        "type": "string",
                        "enum": list(_INVESTOR_KEY_TO_PYKRX.keys()),
                        "description": (
                            "투자자 유형 canonical 키. "
                            "private_fund=사모, pension=연기금, foreign=외국인, "
                            "institutional=기관합계, individual=개인, "
                            "financial_investment=금융투자, insurance=보험, "
                            "investment_trust=투신, bank=은행, other_finance=기타금융, "
                            "other_corp=기타법인, other_foreign=기타외국인"
                        ),
                    },
                    "market": {
                        "type": "string",
                        "enum": list(_DISCOVERY_MARKETS),
                        "description": (
                            "조회 시장. ALL=KOSPI+KOSDAQ 통합 (기본). "
                            "특정 시장만 보고 싶으면 KOSPI 또는 KOSDAQ."
                        ),
                    },
                    "period_days": {
                        "type": "integer",
                        "description": (
                            "오늘로부터 N일 전 ~ 오늘 구간의 누적 순매수를 집계. "
                            f"기본 30, 최대 {_MAX_PERIOD_DAYS}."
                        ),
                    },
                    "top_n": {
                        "type": "integer",
                        "description": (
                            f"상위 몇 종목을 반환할지. 기본 {_DEFAULT_TOP_N}, 최대 {_MAX_TOP_N}."
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": (
                            "buy=순매수 상위 (가장 많이 산 종목). "
                            "sell=순매도 상위 (가장 많이 판 종목). 기본 buy."
                        ),
                    },
                },
                "required": ["investor_type"],
            },
        ),
        executor=_exec_discover_by_investor_flow,
    ),
}


def all_discovery_tool_definitions() -> list[ToolDef]:
    return [spec.definition for spec in DISCOVERY_TOOLS.values()]


# Re-export for tests / introspection.
__all__ = [
    "DISCOVERY_TOOLS",
    "DiscoveryCandidate",
    "DiscoveryToolResult",
    "DiscoveryToolSpec",
    "all_discovery_tool_definitions",
]
