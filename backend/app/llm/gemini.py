"""Google Gemini implementation of LLMClient (google-genai SDK) — with
function calling.

Gemini's tool model differs from Anthropic's in subtle ways:
  - Tools are wrapped in `types.Tool(function_declarations=[...])`.
  - Each function call in the response is a `Part(function_call=...)`
    without an explicit id field — Gemini matches results by name.
    We synthesize a stable id for our own ToolCall accounting so
    higher-level code (multi-turn) treats both providers identically.
  - Results are returned as `Part(function_response=...)` on a "user"
    role turn.
"""

from __future__ import annotations

import structlog
from google import genai
from google.genai import types

from app.llm.base import (
    AskResult,
    ChatMessage,
    LLMClient,
    ToolCall,
    ToolDef,
)
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
        tools: list[ToolDef] | None = None,
    ) -> AskResult:
        if self._client is None:
            raise RuntimeError(
                "Gemini client not configured. Set GEMINI_API_KEY in .env."
            )

        await self._budget.check()

        contents = [_chat_msg_to_gemini(m) for m in messages]
        config_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": self._max_output_tokens,
        }
        if tools:
            config_kwargs["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters=t.parameters,
                        )
                        for t in tools
                    ]
                )
            ]

        response = await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            for i, part in enumerate(parts):
                if getattr(part, "function_call", None) is not None:
                    fc = part.function_call
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(
                        ToolCall(
                            # Gemini lacks an explicit call id; synthesize a
                            # stable per-response one so multi-turn code can
                            # round-trip uniformly. (Round-trip itself matches
                            # by `name`, not id, on Gemini.)
                            id=f"gem-{i}-{fc.name}",
                            name=fc.name,
                            arguments=args,
                        )
                    )
                else:
                    t = getattr(part, "text", None)
                    if t:
                        text_parts.append(t)

        # response.text is the same content joined — but only when there are
        # no function_call parts. Use our manual aggregation to be safe.
        text = "\n".join(text_parts).strip()

        usage = getattr(response, "usage_metadata", None)
        in_tok = (getattr(usage, "prompt_token_count", 0) if usage else 0) or 0
        out_tok = (getattr(usage, "candidates_token_count", 0) if usage else 0) or 0
        await self._budget.record(in_tok, out_tok)

        return AskResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=model,
            tool_calls=tool_calls,
        )

    async def aclose(self) -> None:
        # google-genai's Client has no explicit close; underlying httpx client
        # is closed when the object is garbage-collected.
        pass


def _chat_msg_to_gemini(m: ChatMessage) -> types.Content:
    """Map ChatMessage → Gemini Content. Gemini uses role="model" for the
    assistant; "user" stays "user"."""
    role = "model" if m.role == "assistant" else "user"

    parts: list[types.Part] = []
    if m.content:
        parts.append(types.Part(text=m.content))

    for call in m.tool_calls:
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    name=call.name,
                    args=call.arguments,
                )
            )
        )
    for result in m.tool_results:
        # Gemini's function_response carries a `response` dict; convention is
        # to nest under "result" (success) or "error" (failure).
        payload = (
            {"error": result.content}
            if result.is_error
            else {"result": result.content}
        )
        # The tool name is needed to bind back; we extracted it from
        # tool_call_id by convention (gem-<idx>-<name>). Fallback to the
        # whole id if parsing fails.
        name = _extract_name(result.tool_call_id)
        parts.append(
            types.Part(
                function_response=types.FunctionResponse(
                    name=name,
                    response=payload,
                )
            )
        )

    return types.Content(role=role, parts=parts)


def _extract_name(tool_call_id: str) -> str:
    """`gem-<idx>-<name>` → `<name>`. For other formats (e.g. Anthropic
    toolu_*) returns the id unchanged — Anthropic uses ids end-to-end so
    this code path won't hit on Anthropic round-trips."""
    if tool_call_id.startswith("gem-"):
        # Skip "gem-" and one numeric index segment, take the rest.
        parts = tool_call_id.split("-", 2)
        if len(parts) == 3:
            return parts[2]
    return tool_call_id
