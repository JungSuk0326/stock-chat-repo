"""POST /chat — LLM 상담 엔드포인트 + tool-use 통합 (Top5).

기존 chat 흐름에 multi-turn tool use를 얹는다:

  1. 종목 컨텍스트 + 시스템 프롬프트 + 세션 메시지 history를 LLM에 전달
     (tools=alert tools 동봉)
  2. LLM이 tool_calls를 응답하면:
     - 읽기 도구(list_alerts) → 즉시 실행, 결과를 tool_result로 다시 LLM에 던지고 반복
     - 쓰기 도구(create_alert_rule, delete_alert) → 실행 X, 응답의
       `pending_actions`에 담아 프론트에 반환. 사용자가 [확인] 누를 때
       POST /chat/tool-confirm가 실제 실행
  3. tool_calls가 없는 응답이 오면 종료
  4. 무한 루프 방지: MAX_TOOL_ROUNDS

세션 영속 정책 (Top3와 동일):
- 사용자 질문 + 최종 assistant 답변만 chat_messages에 저장
- 중간 round-trip(assistant tool_use / user tool_result)은 휘발
- /chat/tool-confirm 성공 시 "✅ ... 완료" 한 줄을 transcript에 추가
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.config import get_settings
from app.core.db import get_db
from app.core.redis_client import redis_client
from app.llm.base import ChatMessage, ToolResult
from app.llm.budget import LLMBudgetExceeded
from app.llm.catalog import get_model
from app.llm.registry import LLMRegistry
from app.llm.tools import MAX_WRITE_CALLS_PER_RESPONSE, TOOLS, all_tool_definitions
from app.models import Instrument
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    PendingAction,
    ToolConfirmRequest,
    ToolConfirmResponse,
)
from app.services.chat_history import (
    append_message,
    create_session,
    get_session,
    list_messages,
    schedule_auto_title,
)
from app.services.llm_context import assemble_context

log = structlog.get_logger()

router = APIRouter(prefix="/chat", tags=["chat"])

# Bound the read-tool loop. Practically, "list_alerts then answer" finishes
# in 2 rounds; we leave headroom for an occasional second probe.
MAX_TOOL_ROUNDS = 3

SYSTEM_PROMPT = """\
당신은 개인 투자자 한 사람만 사용하는 주식 모니터링 앱의 상담 어시스턴트다.

- 사용자는 지금 특정 종목 차트를 보고 있다. 그 종목의 실시간 시세/추세/기술적 지표가
  아래 "현재 컨텍스트"에 들어 있다. 답변할 때 이 컨텍스트의 숫자를 근거로 활용해라.
- "사세요/파세요" 같은 단정적 매매 추천은 하지 마라. 시그널과 리스크를 균형 있게 짚어라.
- 자동매매를 하지 않는 앱이다. 즉시 매매 의사결정 가이드보다는 분석/관찰 톤이 적합.
- 일반 금융/투자 지식 (용어, 지표 설명, 시장 일반론, 경제 개념, 한국 주식 시장 제도 등)은
  당신의 학습 지식으로 자연스럽게 설명해라. 예: "DART", "PER", "스톡옵션",
  "공매도", "ETF" 같은 일반 용어 질문은 검증된 일반 지식으로 답하면 된다.
- 추측하면 안 되는 건 **이 종목의 구체적인 시세/공시/뉴스 내용** 등 컨텍스트
  바깥의 사실 데이터다. 컨텍스트에 없는 숫자나 사건은 만들어내지 마라.
- 한국어로, 마크다운 가독성 있게, 간결하게 답해라. 필요하면 표/리스트 사용.

도구 사용 (중요):
- 사용자가 알림 조회/생성/삭제를 요청하면 즉시 해당 도구(list_alerts /
  create_alert_rule / delete_alert)를 호출해라. 자연어로 "이렇게 등록할까요?"
  되묻지 말 것 — 시스템이 자동으로 확인 카드를 띄워서 사용자가 [확인] 또는
  [취소]를 누른다.
- 종목 코드(exchange, symbol)는 현재 컨텍스트에 명시된 것을 그대로 쓰고, 사용자가
  다른 종목을 명시적으로 말하지 않는 한 추측하지 말 것.
- 도구를 호출한 직후의 자연어 응답은 짧게 "삼성전자 360,000원 돌파 알림을
  등록 요청했습니다. 확인 카드를 확인해 주세요." 정도면 충분하다.
- threshold가 명백히 비현실적(예: 음수, 0)인 경우는 호출하지 말고 사용자에게
  되물어라.
"""


def _registry(request: Request) -> LLMRegistry:
    return request.app.state.llm_registry


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    registry: LLMRegistry = Depends(_registry),
    user_id: int = Depends(get_current_user_id),
) -> ChatResponse:
    settings = get_settings()
    provider = (body.provider or settings.LLM_DEFAULT_PROVIDER).strip().lower()
    model_id = (body.model or settings.LLM_DEFAULT_MODEL).strip()

    if get_model(provider, model_id) is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model: {provider}:{model_id}",
        )

    client = registry.get(provider)
    if client is None or not client.configured:
        raise HTTPException(
            status_code=503,
            detail=f"LLM provider not configured: {provider}",
        )

    exchange = body.exchange.upper().strip()
    symbol = body.symbol.strip()

    instrument = (
        await db.execute(
            select(Instrument).where(
                Instrument.exchange == exchange,
                Instrument.symbol == symbol,
            )
        )
    ).scalar_one_or_none()
    if instrument is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument not found: {exchange}:{symbol}",
        )

    context = await assemble_context(db, redis_client, exchange, symbol)
    if context is None:
        raise HTTPException(status_code=404, detail="Instrument not found")

    context_text = context.as_text()
    system = (
        SYSTEM_PROMPT
        + "\n\n### 현재 컨텍스트 (이 종목의 최신 데이터)\n"
        + context_text
    )

    # Session resolution (3 modes, same as Top3)
    ephemeral_mode = body.session_id is None and body.history is not None

    session = None
    is_first_exchange = False
    history: list[ChatMessage]

    if ephemeral_mode:
        history = [
            ChatMessage(role=h.role, content=h.content) for h in (body.history or [])
        ]
    else:
        if body.session_id is not None:
            try:
                session = await get_session(db, body.session_id, user_id)
            except NoResultFound:
                raise HTTPException(status_code=404, detail="Session not found")
            if session.instrument_id != instrument.id:
                raise HTTPException(
                    status_code=400,
                    detail="Session belongs to a different instrument",
                )
            stored = await list_messages(db, session_id=session.id)
            history = [ChatMessage(role=m.role, content=m.content) for m in stored]
            is_first_exchange = len(stored) == 0
        else:
            session = await create_session(
                db, user_id=user_id, instrument_id=instrument.id, title=None
            )
            history = []
            is_first_exchange = True

    # Build the running message list for the tool loop. Mutated across rounds.
    messages: list[ChatMessage] = list(history)
    messages.append(ChatMessage(role="user", content=body.question))

    tool_defs = all_tool_definitions()
    pending_actions: list[PendingAction] = []

    total_input_tokens = 0
    total_output_tokens = 0
    final_text = ""
    final_model = model_id

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            result = await client.ask(
                system=system,
                messages=messages,
                model=model_id,
                tools=tool_defs,
            )
            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens
            final_model = result.model

            if not result.tool_calls:
                final_text = result.text
                break

            write_calls = [
                c for c in result.tool_calls if TOOLS[c.name].requires_confirmation
            ]
            read_calls = [
                c
                for c in result.tool_calls
                if not TOOLS[c.name].requires_confirmation
            ]

            if write_calls:
                # Defer all writes; ignore any read calls in the same response
                # (rare — usually the model picks one type per turn).
                for call in write_calls[:MAX_WRITE_CALLS_PER_RESPONSE]:
                    if call.name not in TOOLS:
                        log.warning("chat.tool_unknown", name=call.name)
                        continue
                    spec = TOOLS[call.name]
                    summary = (
                        spec.summarizer(call.arguments) if spec.summarizer else ""
                    )
                    pending_actions.append(
                        PendingAction(
                            tool_call_id=call.id,
                            name=call.name,
                            arguments=call.arguments,
                            summary=summary,
                        )
                    )
                final_text = result.text or "위 작업을 등록할까요?"
                break

            # Pure read calls — execute, append round-trip messages, loop.
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=result.text,
                    tool_calls=read_calls,
                )
            )
            tool_results: list[ToolResult] = []
            for call in read_calls:
                spec = TOOLS.get(call.name)
                if spec is None:
                    tool_results.append(
                        ToolResult(
                            tool_call_id=call.id,
                            content=f"Unknown tool: {call.name}",
                            is_error=True,
                        )
                    )
                    continue
                try:
                    content = await spec.executor(call.arguments, db, user_id)
                    tool_results.append(
                        ToolResult(tool_call_id=call.id, content=content)
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "chat.tool_exec_failed", name=call.name
                    )
                    tool_results.append(
                        ToolResult(
                            tool_call_id=call.id,
                            content=str(exc),
                            is_error=True,
                        )
                    )
            messages.append(ChatMessage(role="user", tool_results=tool_results))
        else:
            # Loop exhausted without breaking
            final_text = (
                final_text or "도구 호출 라운드 한도에 도달했습니다. 다시 질문해 주세요."
            )
    except LLMBudgetExceeded as exc:
        log.warning("chat.budget_exceeded", error=str(exc))
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "chat.llm_error", provider=provider, model=model_id, error=str(exc)
        )
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    # Persist user + final assistant only (round-trip middle layers are
    # ephemeral). Ephemeral mode skips persistence entirely.
    if session is not None:
        await append_message(
            db, session_id=session.id, role="user", content=body.question
        )
        await append_message(
            db,
            session_id=session.id,
            role="assistant",
            content=final_text,
            model=final_model,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )
        if is_first_exchange and session.title is None:
            schedule_auto_title(
                registry,
                session_id=session.id,
                user_question=body.question,
                instrument_name=instrument.name,
                canonical_id=f"{instrument.exchange}:{instrument.symbol}",
            )

    return ChatResponse(
        answer=final_text,
        instrument=context.canonical_id,
        provider=provider,
        model=final_model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        context_preview=context_text,
        session_id=session.id if session is not None else None,
        pending_actions=pending_actions,
    )


@router.post("/tool-confirm", response_model=ToolConfirmResponse)
async def tool_confirm(
    body: ToolConfirmRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ToolConfirmResponse:
    """Execute a write tool that the LLM previously proposed.

    The frontend's confirmation card POSTs here when the user clicks [확인].
    `name` and `arguments` are echoed back (not re-derived from session
    storage) — we trust the request body but the tool's executor is the
    same one that would have run had we executed inline, so all the same
    validation applies.

    Session ownership is verified before appending a transcript note; if
    the session_id is invalid the action still runs (it's owner-scoped via
    user_id) but the note is skipped.
    """
    spec = TOOLS.get(body.name)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {body.name}")
    if not spec.requires_confirmation:
        # Read tools shouldn't go through confirm — would let the UI bypass
        # the LLM and arbitrarily execute. Defensive guard.
        raise HTTPException(
            status_code=400,
            detail=f"Tool {body.name} does not require confirmation",
        )

    try:
        result_text = await spec.executor(body.arguments, db, user_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("tool_confirm.failed", name=body.name)
        return ToolConfirmResponse(ok=False, result=str(exc))

    # Tool executors return Korean prose; "완료" marks success.
    ok = "완료" in result_text

    if body.session_id is not None:
        try:
            await get_session(db, body.session_id, user_id)
            await append_message(
                db,
                session_id=body.session_id,
                role="assistant",
                content=("✅ " if ok else "⚠️ ") + result_text,
            )
        except NoResultFound:
            log.warning(
                "tool_confirm.session_not_found",
                session_id=body.session_id,
                name=body.name,
            )

    log.info(
        "tool_confirm.done",
        name=body.name,
        ok=ok,
        user_id=user_id,
    )
    return ToolConfirmResponse(ok=ok, result=result_text)
