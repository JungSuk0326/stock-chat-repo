"""POST /discovery/llm — natural-language stock discovery.

User types something like "근 1개월간 사모펀드에서 가장 많이 순매수한 종목" →
the LLM picks the right tool args → tool calls pykrx (cached via Redis) →
results come back as both natural-language prose AND a structured
candidates list for the frontend.

Differences vs `POST /chat`:
  - stateless (no session, no message persistence)
  - only the discovery tool registry is exposed
  - no symbol context (this is market-wide search, not "about this stock")
  - tool results are accumulated; candidates land in the response payload
    in addition to the LLM's prose

The same multi-turn tool loop pattern is used (LLM → tool exec →
LLM final answer) with a low MAX_TOOL_ROUNDS since a single round
covers the common case.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.redis_client import redis_client
from app.llm.base import ChatMessage, ToolResult
from app.llm.budget import LLMBudgetExceeded
from app.llm.catalog import get_model
from app.llm.discovery_tools import (
    DISCOVERY_TOOLS,
    DiscoveryCandidate,
    all_discovery_tool_definitions,
)
from app.llm.registry import LLMRegistry
from app.schemas.discovery_llm import (
    DiscoveryCandidateOut,
    DiscoveryLlmRequest,
    DiscoveryLlmResponse,
)
from app.core.config import get_settings

log = structlog.get_logger()

router = APIRouter(prefix="/discovery", tags=["discovery"])


# Two rounds = one tool call + one final answer. The discovery tools are
# read-only so there's no human-in-the-loop confirmation to break the loop
# early; we just cap iterations to keep token usage bounded.
MAX_TOOL_ROUNDS = 3


SYSTEM_PROMPT = """\
당신은 한국 주식 시장에서 종목을 발굴해주는 도우미다. 사용자가 자연어로 발굴
조건을 말하면 알맞은 도구를 호출해서 데이터를 가져오고, 결과를 한국어 prose로
정리해 답해라.

규칙:
- "사모펀드", "연기금", "외국인", "기관" 같은 표현은 `discover_by_investor_flow`
  도구의 investor_type 인자(canonical key)로 매핑한다. 예:
    "사모펀드" / "사모" → private_fund
    "연기금" → pension
    "외국인" → foreign
    "기관"  → institutional (= 기관합계)
    "개인"  → individual
- 기간 표현은 period_days (정수, 일 단위)로 변환:
    "근 1개월" / "최근 한 달" → 30
    "최근 1주일" → 7
    "지난 3개월" / "근 한 분기" → 90
- 시장이 명시되지 않으면 market="ALL" (KOSPI+KOSDAQ 통합).
- "가장 많이 산" / "순매수 상위" → direction="buy"
  "가장 많이 판" / "순매도 상위" → direction="sell"
- top_n은 사용자가 명시한 숫자(예: "top 5", "상위 10개")가 있으면 그대로 쓰고,
  없으면 10.

답변:
- 도구 결과를 그대로 마크다운 리스트로 베껴 적지 말 것. 사용자에게 의미 있는
  요약 한두 문장 + 상위 5~10개 종목을 간단히 정리하면 된다.
- "이 종목을 사세요/파세요" 같은 단정적 매매 추천은 금지.
- 도구가 비어 있는 결과를 돌려주면 그대로 사용자에게 알린다 (KRX 로그인 누락,
  데이터 없음 등 가능한 원인 짧게 언급).
- 사용자가 발굴 의도와 무관한 일반 질문을 하면 "이 화면은 종목 발굴 전용입니다"
  라고 짧게 안내하고 도구는 호출하지 마라.
"""


def _registry(request: Request) -> LLMRegistry:
    return request.app.state.llm_registry


@router.post("/llm", response_model=DiscoveryLlmResponse)
async def discovery_llm_query(
    body: DiscoveryLlmRequest,
    db: AsyncSession = Depends(get_db),
    registry: LLMRegistry = Depends(_registry),
) -> DiscoveryLlmResponse:
    settings = get_settings()
    provider = (body.provider or settings.LLM_DEFAULT_PROVIDER).strip().lower()
    model_id = (body.model or settings.LLM_DEFAULT_MODEL).strip()

    if get_model(provider, model_id) is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown model: {provider}:{model_id}"
        )

    client = registry.get(provider)
    if client is None or not client.configured:
        raise HTTPException(
            status_code=503, detail=f"LLM provider not configured: {provider}"
        )

    tool_defs = all_discovery_tool_definitions()
    messages: list[ChatMessage] = [ChatMessage(role="user", content=body.query)]

    accumulated_candidates: list[DiscoveryCandidate] = []
    tools_called: list[dict] = []
    total_input = 0
    total_output = 0
    final_text = ""
    final_model = model_id

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            result = await client.ask(
                system=SYSTEM_PROMPT,
                messages=messages,
                model=model_id,
                tools=tool_defs,
            )
            total_input += result.input_tokens
            total_output += result.output_tokens
            final_model = result.model

            if not result.tool_calls:
                final_text = result.text
                break

            # All discovery tools are read-only → execute immediately.
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=result.text,
                    tool_calls=result.tool_calls,
                )
            )
            tool_results: list[ToolResult] = []
            for call in result.tool_calls:
                spec = DISCOVERY_TOOLS.get(call.name)
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
                    res = await spec.executor(call.arguments, db, redis_client)
                except Exception as exc:  # noqa: BLE001 — pykrx wraps many errors
                    log.exception("discovery_llm.tool_exec_failed", name=call.name)
                    tool_results.append(
                        ToolResult(
                            tool_call_id=call.id, content=str(exc), is_error=True
                        )
                    )
                    continue
                accumulated_candidates.extend(res.candidates)
                tools_called.append(
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "candidate_count": len(res.candidates),
                    }
                )
                tool_results.append(
                    ToolResult(tool_call_id=call.id, content=res.text)
                )
            messages.append(ChatMessage(role="user", tool_results=tool_results))
        else:
            final_text = (
                final_text
                or "도구 호출 라운드 한도에 도달했습니다. 다시 질문해 주세요."
            )
    except LLMBudgetExceeded as exc:
        # R2 — combined daily/monthly token cap hit. Same surface as /chat.
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    # Deduplicate candidates by (exchange, symbol) preserving first occurrence;
    # the LLM may call the tool twice with overlapping params.
    seen: set[tuple[str, str]] = set()
    deduped: list[DiscoveryCandidateOut] = []
    for c in accumulated_candidates:
        key = (c.exchange, c.symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            DiscoveryCandidateOut(
                exchange=c.exchange,
                symbol=c.symbol,
                name=c.name,
                metric_label=c.metric_label,
                metric_value=c.metric_value,
            )
        )

    return DiscoveryLlmResponse(
        answer=final_text,
        candidates=deduped,
        tools_called=tools_called,
        input_tokens=total_input,
        output_tokens=total_output,
        model=final_model,
    )
