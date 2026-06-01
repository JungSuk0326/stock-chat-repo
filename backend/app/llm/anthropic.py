"""Anthropic Claude implementation of LLMClient — with tool use."""

from __future__ import annotations

from typing import Any

import structlog
from anthropic import AsyncAnthropic

from app.llm.base import (
    AskResult,
    ChatMessage,
    LLMClient,
    ToolCall,
    ToolDef,
)
from app.llm.budget import LLMBudget

log = structlog.get_logger()


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        budget: LLMBudget,
        max_output_tokens: int = 2048,
    ) -> None:
        if not api_key:
            log.warning("llm.anthropic.no_api_key")
        self._client = AsyncAnthropic(api_key=api_key) if api_key else None
        self._budget = budget
        self._max_output_tokens = max_output_tokens

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def ask(
        self,
        system: str,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDef] | None = None,
    ) -> AskResult:
        if self._client is None:
            raise RuntimeError(
                "Anthropic client not configured. Set ANTHROPIC_API_KEY in .env."
            )

        await self._budget.check()

        api_messages = [_chat_msg_to_anthropic(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_output_tokens,
            "system": system,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        response = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )
        text = "\n".join(text_parts).strip()

        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        await self._budget.record(in_tok, out_tok)

        return AskResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=model,
            tool_calls=tool_calls,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


def _chat_msg_to_anthropic(m: ChatMessage) -> dict[str, Any]:
    """Map ChatMessage → Anthropic Messages API shape.

    Plain text → simple `{"role", "content": "..."}`.
    Tool round-trip → `{"role", "content": [block, block, ...]}` with
      text + tool_use (assistant) or tool_result (user) blocks.
    """
    if not m.tool_calls and not m.tool_results:
        return {"role": m.role, "content": m.content}

    blocks: list[dict[str, Any]] = []
    if m.content:
        blocks.append({"type": "text", "text": m.content})

    for call in m.tool_calls:
        blocks.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
        )
    for result in m.tool_results:
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": result.tool_call_id,
                "content": result.content,
                "is_error": result.is_error,
            }
        )
    return {"role": m.role, "content": blocks}
