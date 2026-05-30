"""POST /chat — LLM 상담 엔드포인트.

Stateless: 클라이언트가 매번 history를 보냄. Backend는:
  1. 현재 종목 컨텍스트(assemble_context)를 만들어
  2. 그걸 system prompt에 주입한 뒤
  3. user/assistant history + 새 question을 함께 LLM에 보낸다.

provider/model은 클라이언트가 매 호출마다 선택. 미지정 시 settings 기본값
사용. 등록되지 않은 provider/model은 400.

LLM 토큰 cap(R2)은 LLMBudget(공통)이 모든 provider 통합으로 강제. 초과
시 429.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.redis_client import redis_client
from app.llm.base import ChatMessage
from app.llm.budget import LLMBudgetExceeded
from app.llm.catalog import get_model
from app.llm.registry import LLMRegistry
from app.schemas.chat import ChatRequest, ChatResponse
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
) -> ChatResponse:
    settings = get_settings()
    provider = (body.provider or settings.LLM_DEFAULT_PROVIDER).strip().lower()
    model_id = (body.model or settings.LLM_DEFAULT_MODEL).strip()

    # 카탈로그에 없는 (provider, model_id) 조합은 거절 — 클라이언트가 임의
    # 모델 id를 보내서 SDK에 그대로 흘러가는 일을 막는다.
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

    context = await assemble_context(db, redis_client, exchange, symbol)
    if context is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument not found: {exchange}:{symbol}",
        )

    context_text = context.as_text()
    system = (
        SYSTEM_PROMPT
        + "\n\n### 현재 컨텍스트 (이 종목의 최신 데이터)\n"
        + context_text
    )

    messages: list[ChatMessage] = [
        ChatMessage(role=h.role, content=h.content) for h in body.history
    ]
    messages.append(ChatMessage(role="user", content=body.question))

    try:
        result = await client.ask(system=system, messages=messages, model=model_id)
    except LLMBudgetExceeded as exc:
        log.warning("chat.budget_exceeded", error=str(exc))
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("chat.llm_error", provider=provider, model=model_id, error=str(exc))
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    return ChatResponse(
        answer=result.text,
        instrument=context.canonical_id,
        provider=provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        context_preview=context_text,
    )
