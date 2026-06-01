"""LLM provider abstraction.

Every provider implementation (Anthropic, Gemini, OpenAI, ...) returns the
same ChatMessage/AskResult shapes so higher-level code (api/chat.py,
services/llm_context.py) is provider-agnostic.

Each implementation owns its own SDK quirks (Anthropic's `system=` kwarg vs
Gemini's `Content(role="model"|"user")`, token-usage field naming, tool/
function-calling shape, etc.) inside its module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Output of executing one tool call, paired back with its `tool_call_id`.

    Carried on a user-role ChatMessage in the round-trip after the assistant
    requested the call. `is_error` lets us tell the model the call failed
    so it can apologize / retry instead of pretending it worked.
    """

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ChatMessage:
    """A turn in the conversation.

    role: "user" | "assistant" — provider-specific role names (Gemini uses
    "model" instead of "assistant", Anthropic uses "assistant") are mapped
    inside each implementation. Callers always use these two names.

    Most messages are plain text — only `content` is set. The other two
    fields are non-empty only during a tool-use round-trip:
      - assistant message that requests tools → `tool_calls` populated
        (content may be empty or have a brief reasoning preamble)
      - user message that delivers tool results → `tool_results` populated
        (content should be empty)
    """

    role: str
    content: str = ""
    tool_calls: "list[ToolCall]" = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class ToolDef:
    """Provider-agnostic tool definition. Each provider adapter maps this
    to its own SDK format (Anthropic `tools=[{name, description, input_schema}]`,
    Gemini `FunctionDeclaration(name, description, parameters)`).

    `parameters` is a JSON Schema-ish dict describing the tool's input.
    Both Anthropic and Gemini accept JSON-Schema-shaped dicts, so we keep
    the source of truth here as raw dict.
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """One tool invocation requested by the LLM. `id` is the call id —
    Anthropic generates it (`toolu_*`); for Gemini we synthesize one.
    Subsequent rounds of the same conversation must echo this id back
    when delivering the result, so we can stitch turns correctly.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AskResult:
    """Output of one `ask()` call.

    If the model decided to call tools, `tool_calls` is non-empty AND the
    caller is expected to handle them (or surface to the user for
    confirmation). `text` may be empty in that case — the model often
    emits just the tool calls without prose.
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str  # the exact model id used (e.g. "gemini-2.5-pro")
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(ABC):
    """Async LLM client. One instance per (provider, api-key) — the registry
    holds long-lived clients keyed by provider for the lifetime of the app."""

    #: Provider identifier — matches the LLMModel.provider in catalog.py
    provider: str

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True if a valid API key was provided. Used so the registry can
        omit a provider whose key is missing."""
        ...

    @abstractmethod
    async def ask(
        self,
        system: str,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDef] | None = None,
    ) -> AskResult:
        """Send `messages` (alternating user/assistant) under `system` prompt
        to `model`. When `tools` is provided, the model may respond with
        `tool_calls` (and possibly empty `text`); caller dispatches them
        and may follow up with another `ask()` carrying the tool results
        in subsequent ChatMessages.

        Raises LLMBudgetExceeded before issuing the request if the token
        cap is already hit. Records token usage after the call.
        """
        ...

    @abstractmethod
    async def aclose(self) -> None:
        """Cleanup the underlying SDK client. Called from lifespan shutdown."""
        ...
