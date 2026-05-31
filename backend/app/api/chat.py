"""POST /chat — LLM 상담 엔드포인트.

세션 영속화 통합:
  - 클라이언트가 session_id를 보내면 해당 세션 메시지를 DB에서 replay해서
    LLM에 history로 전달
  - session_id가 없으면 (user, instrument) 새 세션을 자동 생성
  - 호출 후 user/assistant 메시지를 모두 chat_messages에 INSERT
  - 신규 세션 + 첫 메시지 교환이면 백그라운드로 자동 타이틀 생성

provider/model은 클라이언트가 매 호출마다 선택. 미지정 시 settings 기본값
사용. 등록되지 않은 provider/model은 400.

토큰 cap(R2)은 LLMBudget(공통)이 모든 provider 통합으로 강제. 초과 시 429.
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
from app.llm.base import ChatMessage
from app.llm.budget import LLMBudgetExceeded
from app.llm.catalog import get_model
from app.llm.registry import LLMRegistry
from app.models import Instrument
from app.schemas.chat import ChatRequest, ChatResponse
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

SYSTEM_PROMPT = """\
당신은 개인 투자자 한 사람만 사용하는 주식 모니터링 앱의 상담 어시스턴트다.

- 사용자는 지금 특정 종목 차트를 보고 있다. 그 종목의 실시간 시세/추세/기술적 지표가
  아래 "현재 컨텍스트"에 들어 있다. 답변할 때 이 컨텍스트의 숫자를 근거로 활용해라.
- "사세요/파세요" 같은 단정적 매매 추천은 하지 마라. 시그널과 리스크를 균형 있게 짚어라.
- 자동매매를 하지 않는 앱이다. 즉시 매매 의사결정 가이드보다는 분석/관찰 톤이 적합.
- 모른다고 답해야 할 때는 솔직히 모른다고 말해라. 데이터에 없는 내용은 추측하지 마라.
- 한국어로, 마크다운 가독성 있게, 간결하게 답해라. 필요하면 표/리스트 사용.
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
        # assemble_context only returns None when the instrument is missing;
        # we already 404'd above, so this is defensive.
        raise HTTPException(status_code=404, detail="Instrument not found")

    context_text = context.as_text()
    system = (
        SYSTEM_PROMPT
        + "\n\n### 현재 컨텍스트 (이 종목의 최신 데이터)\n"
        + context_text
    )

    # ----- Session + history -----
    # Three modes:
    #   1) session_id given → load + use existing history
    #   2) session_id missing AND body.history is None → auto-create session
    #   3) session_id missing AND body.history given → ephemeral mode, no
    #      persistence (legacy clients / one-shot integrations)
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

    messages: list[ChatMessage] = list(history)
    messages.append(ChatMessage(role="user", content=body.question))

    try:
        result = await client.ask(system=system, messages=messages, model=model_id)
    except LLMBudgetExceeded as exc:
        log.warning("chat.budget_exceeded", error=str(exc))
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "chat.llm_error", provider=provider, model=model_id, error=str(exc)
        )
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    # Persist both turns. Skip on ephemeral mode.
    if session is not None:
        await append_message(
            db, session_id=session.id, role="user", content=body.question
        )
        await append_message(
            db,
            session_id=session.id,
            role="assistant",
            content=result.text,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
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
        answer=result.text,
        instrument=context.canonical_id,
        provider=provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        context_preview=context_text,
        session_id=session.id if session is not None else None,
    )
