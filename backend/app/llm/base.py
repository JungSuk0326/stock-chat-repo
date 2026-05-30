"""LLM provider abstraction.

Every provider implementation (Anthropic, Gemini, OpenAI, ...) returns the
same ChatMessage/AskResult shapes so higher-level code (api/chat.py,
services/llm_context.py) is provider-agnostic.

Each implementation owns its own SDK quirks (Anthropic's `system=` kwarg vs
Gemini's `Content(role="model"|"user")`, token-usage field naming, etc.)
inside its module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChatMessage:
    """A turn in the conversation.

    role: "user" | "assistant" — provider-specific role names (Gemini uses
    "model" instead of "assistant", Anthropic uses "assistant") are mapped
    inside each implementation. Callers always use these two names.
    """

    role: str
    content: str


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str  # the exact model id used (e.g. "gemini-2.5-pro")


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
    ) -> AskResult:
        """Send `messages` (alternating user/assistant) under `system` prompt
        to `model`. Raises LLMBudgetExceeded before issuing the request if the
        token cap is already hit. Records token usage after the call."""
        ...

    @abstractmethod
    async def aclose(self) -> None:
        """Cleanup the underlying SDK client. Called from lifespan shutdown."""
        ...
