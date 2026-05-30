"""Anthropic Claude implementation of LLMClient."""

from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic

from app.llm.base import AskResult, ChatMessage, LLMClient
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
    ) -> AskResult:
        if self._client is None:
            raise RuntimeError(
                "Anthropic client not configured. Set ANTHROPIC_API_KEY in .env."
            )

        await self._budget.check()

        response = await self._client.messages.create(
            model=model,
            max_tokens=self._max_output_tokens,
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )

        text_parts = [
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ]
        text = "\n".join(text_parts).strip()

        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        await self._budget.record(in_tok, out_tok)

        return AskResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=model,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
