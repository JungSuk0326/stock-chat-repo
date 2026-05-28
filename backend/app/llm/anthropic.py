"""Thin async wrapper over the Anthropic SDK.

Every call:
  1. checks the LLMBudget (raises LLMBudgetExceeded if over cap)
  2. issues the Anthropic request
  3. records token usage to the budget

Higher-level callers (services/llm_context.py, api/chat.py) talk to this class
and never touch the SDK directly. Makes it easy to swap models or back the
provider with a mock for tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from anthropic import AsyncAnthropic

from app.llm.budget import LLMBudget

log = structlog.get_logger()


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


@dataclass
class ChatMessage:
    """Anthropic Messages API shape: role is 'user' or 'assistant'."""

    role: str  # "user" | "assistant"
    content: str


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        budget: LLMBudget,
        max_output_tokens: int = 2048,
    ) -> None:
        if not api_key:
            # Allow construction without a key so the app boots without LLM;
            # the actual call will fail loudly. Lets dev mode run without a key.
            log.warning("llm.anthropic.no_api_key")
        self._client = AsyncAnthropic(api_key=api_key) if api_key else None
        self._model = model
        self._budget = budget
        self._max_output_tokens = max_output_tokens

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def ask(
        self,
        system: str,
        messages: list[ChatMessage],
    ) -> AskResult:
        if self._client is None:
            raise RuntimeError(
                "Anthropic client not configured. Set ANTHROPIC_API_KEY in .env."
            )

        await self._budget.check()

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_output_tokens,
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )

        # Anthropic returns content as a list of blocks (text, tool_use, ...).
        # For plain text chat we expect a single text block.
        text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        text = "\n".join(text_parts).strip()

        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        await self._budget.record(in_tok, out_tok)

        return AskResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=self._model,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
