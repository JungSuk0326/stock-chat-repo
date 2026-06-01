"""LLM-callable tools — alert CRUD via natural language.

Three tools exposed to the model:
  - list_alerts     — READ  (executed immediately, result fed back to LLM)
  - create_alert_rule — WRITE (proposed, requires user confirmation)
  - delete_alert    — WRITE (proposed, requires user confirmation)

`requires_confirmation=True` means the dispatcher does NOT execute the tool
during the /chat round-trip. It surfaces the call to the UI as a pending
action; user clicks [확인] → POST /chat/tool-confirm runs the same executor
function. This way the tool function is the single execution path; no
duplicated logic between "preview" and "real" code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable

import structlog
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import ToolDef
from app.models import Instrument
from app.services.alert_rules import (
    create_rule,
    delete_rule,
    list_rules,
    resolve_instrument,
)

log = structlog.get_logger()

# Max number of write tool calls we surface in one /chat response. Keeps a
# hallucinating model from proposing 50 rules at once.
MAX_WRITE_CALLS_PER_RESPONSE = 3


ToolExecutor = Callable[[dict[str, Any], AsyncSession, int], Awaitable[str]]
"""(arguments, db, user_id) → human-readable result string."""

ToolSummarizer = Callable[[dict[str, Any]], str]
"""(arguments) → one-line preview for confirmation cards."""


@dataclass
class ToolSpec:
    definition: ToolDef
    executor: ToolExecutor
    requires_confirmation: bool
    summarizer: ToolSummarizer | None = None


# ---------- Tool implementations ----------


async def _exec_list_alerts(
    args: dict[str, Any], db: AsyncSession, user_id: int
) -> str:
    exchange = (args.get("exchange") or "").strip().upper() or None
    symbol = (args.get("symbol") or "").strip() or None

    instrument_id: int | None = None
    if exchange and symbol:
        inst = await resolve_instrument(db, exchange, symbol)
        if inst is None:
            return f"종목을 찾을 수 없습니다: {exchange}:{symbol}"
        instrument_id = inst.id
    elif exchange or symbol:
        return "exchange와 symbol은 함께 지정해야 합니다."

    rules = await list_rules(db, user_id=user_id, instrument_id=instrument_id)
    if not rules:
        return "등록된 알림이 없습니다." if instrument_id else "이 종목에 등록된 알림이 없습니다."

    lines: list[str] = []
    for r in rules:
        inst = await db.get(Instrument, r.instrument_id)
        canonical = f"{inst.exchange}:{inst.symbol}" if inst else "?"
        threshold_str = (
            f"{Decimal(r.threshold):,.0f}원"
            if r.condition_type.startswith("price_")
            else f"{Decimal(r.threshold):+.2f}%"
        )
        op = {
            "price_above": "≥",
            "price_below": "≤",
            "pct_change_above": "≥",
            "pct_change_below": "≤",
        }.get(r.condition_type, "?")
        lines.append(
            f"- id={r.id} · {canonical} · {r.name or '(이름 없음)'} · "
            f"{op} {threshold_str} · cooldown {r.cooldown_minutes}분 · "
            f"enabled={r.enabled}"
        )
    return "\n".join(lines)


async def _exec_create_alert_rule(
    args: dict[str, Any], db: AsyncSession, user_id: int
) -> str:
    exchange = (args.get("exchange") or "").strip().upper()
    symbol = (args.get("symbol") or "").strip()
    condition_type = (args.get("condition_type") or "").strip()
    threshold_raw = args.get("threshold")
    name = (args.get("name") or "").strip() or None
    cooldown_minutes = int(args.get("cooldown_minutes") or 60)
    market_hours_only = bool(args.get("market_hours_only", False))

    if not (exchange and symbol and condition_type and threshold_raw is not None):
        return "필수 인자 누락: exchange, symbol, condition_type, threshold"

    inst = await resolve_instrument(db, exchange, symbol)
    if inst is None:
        return f"종목을 찾을 수 없습니다: {exchange}:{symbol}"

    try:
        threshold = Decimal(str(threshold_raw))
    except Exception:
        return f"threshold가 숫자가 아닙니다: {threshold_raw!r}"

    try:
        rule = await create_rule(
            db,
            user_id=user_id,
            instrument_id=inst.id,
            condition_type=condition_type,
            threshold=threshold,
            name=name,
            cooldown_minutes=cooldown_minutes,
            market_hours_only=market_hours_only,
        )
    except ValueError as exc:
        return f"룰 생성 실패: {exc}"

    return f"알림 등록 완료 (id={rule.id})"


async def _exec_delete_alert(
    args: dict[str, Any], db: AsyncSession, user_id: int
) -> str:
    rule_id_raw = args.get("rule_id")
    try:
        rule_id = int(rule_id_raw)
    except (TypeError, ValueError):
        return f"rule_id가 정수가 아닙니다: {rule_id_raw!r}"
    try:
        await delete_rule(db, rule_id=rule_id, user_id=user_id)
    except NoResultFound:
        return f"알림 id={rule_id}를 찾을 수 없습니다."
    return f"알림 id={rule_id} 삭제 완료"


# ---------- Summarizers (for confirmation cards) ----------


def _summarize_create(args: dict[str, Any]) -> str:
    exchange = (args.get("exchange") or "").upper()
    symbol = args.get("symbol") or "?"
    ct = args.get("condition_type") or "?"
    th = args.get("threshold")
    name = args.get("name")
    op_map = {
        "price_above": "≥",
        "price_below": "≤",
        "pct_change_above": "≥",
        "pct_change_below": "≤",
    }
    op = op_map.get(ct, "?")
    metric = "현재가" if ct.startswith("price_") else "전일대비"
    try:
        n = Decimal(str(th))
        threshold_str = (
            f"{int(n):,}원" if ct.startswith("price_") else f"{n:+.2f}%"
        )
    except Exception:
        threshold_str = f"{th}"
    prefix = f"[{name}] " if name else ""
    return f"{prefix}{exchange}:{symbol} {metric} {op} {threshold_str} 알림 등록"


def _summarize_delete(args: dict[str, Any]) -> str:
    return f"알림 id={args.get('rule_id')} 삭제"


# ---------- Tool registry ----------


_CONDITION_ENUM = [
    "price_above",
    "price_below",
    "pct_change_above",
    "pct_change_below",
]

TOOLS: dict[str, ToolSpec] = {
    "list_alerts": ToolSpec(
        definition=ToolDef(
            name="list_alerts",
            description=(
                "현재 사용자의 알림 룰 목록을 조회한다. exchange + symbol을 "
                "함께 지정하면 해당 종목의 알림만 필터링한다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "exchange": {
                        "type": "string",
                        "description": '거래소 코드, 예: "KR"',
                    },
                    "symbol": {
                        "type": "string",
                        "description": '종목 코드, 예: "005930"',
                    },
                },
            },
        ),
        executor=_exec_list_alerts,
        requires_confirmation=False,
    ),
    "create_alert_rule": ToolSpec(
        definition=ToolDef(
            name="create_alert_rule",
            description=(
                "사용자의 알림 룰을 추가한다. 실행 전 사용자 확인이 필요하므로, "
                "이 도구가 호출되면 시스템이 자동으로 확인 카드를 띄운다. "
                "exchange/symbol은 현재 사용자가 보고 있는 종목의 컨텍스트에서 "
                "추론하고, 임의로 지어내지 말 것."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "exchange": {
                        "type": "string",
                        "description": '거래소 코드, 예: "KR"',
                    },
                    "symbol": {
                        "type": "string",
                        "description": '종목 코드, 예: "005930"',
                    },
                    "name": {
                        "type": "string",
                        "description": "사용자가 식별하기 좋은 짧은 이름 (선택)",
                    },
                    "condition_type": {
                        "type": "string",
                        "enum": _CONDITION_ENUM,
                        "description": (
                            "price_above/below: 현재가가 threshold원 이상/이하일 때. "
                            "pct_change_above/below: 전일대비 등락률이 threshold% 이상/이하일 때."
                        ),
                    },
                    "threshold": {
                        "type": "number",
                        "description": "임계값. price_*는 원화 정수, pct_*는 퍼센트 (예: 5 = 5%)",
                    },
                    "cooldown_minutes": {
                        "type": "integer",
                        "description": "한 번 발화 후 다시 발화까지 대기 시간 (기본 60분)",
                    },
                    "market_hours_only": {
                        "type": "boolean",
                        "description": "장중 시간에만 발화 (기본 false)",
                    },
                },
                "required": [
                    "exchange",
                    "symbol",
                    "condition_type",
                    "threshold",
                ],
            },
        ),
        executor=_exec_create_alert_rule,
        requires_confirmation=True,
        summarizer=_summarize_create,
    ),
    "delete_alert": ToolSpec(
        definition=ToolDef(
            name="delete_alert",
            description=(
                "알림 룰을 삭제한다. 실행 전 사용자 확인이 필요하다. "
                "rule_id는 list_alerts로 먼저 조회해 정확한 값을 얻을 것."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "삭제할 알림 룰의 id",
                    },
                },
                "required": ["rule_id"],
            },
        ),
        executor=_exec_delete_alert,
        requires_confirmation=True,
        summarizer=_summarize_delete,
    ),
}


def all_tool_definitions() -> list[ToolDef]:
    """Convenience: every tool's ToolDef for the LLM call."""
    return [spec.definition for spec in TOOLS.values()]


def stringify_args(args: dict[str, Any]) -> str:
    """For logging — args may contain Decimal/None."""
    try:
        return json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        return str(args)
