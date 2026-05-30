"""Google Gemini implementation of LLMClient (google-genai SDK)."""

from __future__ import annotations

import structlog
from google import genai
from google.genai import types

from app.llm.base import AskResult, ChatMessage, LLMClient
from app.llm.budget import LLMBudget

log = structlog.get_logger()


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        budget: LLMBudget,
        max_output_tokens: int = 2048,
    ) -> None:
        if not api_key:
            log.warning("llm.gemini.no_api_key")
            self._client = None
        else:
            self._client = genai.Client(api_key=api_key)
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
                "Gemini client not configured. Set GEMINI_API_KEY in .env."
            )

        await self._budget.check()

        # Gemini uses role="model" for the assistant; map ChatMessage roles.
        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in messages
        ]

        response = await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=self._max_output_tokens,
            ),
        )

        text = (response.text or "").strip()
        usage = response.usage_metadata
        in_tok = (usage.prompt_token_count if usage else 0) or 0
        out_tok = (usage.candidates_token_count if usage else 0) or 0
        await self._budget.record(in_tok, out_tok)

        return AskResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=model,
        )

    async def aclose(self) -> None:
        # google-genai's Client has no explicit close; underlying httpx client
        # is closed when the object is garbage-collected.
        pass
