"""LLM registry — holds long-lived clients keyed by provider.

Built once at app startup (lifespan). Only providers with a configured API
key end up in the registry; that's how the model catalog gets filtered for
the front-end.

Single-user app today, so every provider has at most one client (the
operator's key). When BYOK lands the registry pattern still works — just
add a user_id dimension and a TTL cache.
"""

from __future__ import annotations

import structlog

from app.core.config import Settings
from app.llm.anthropic import AnthropicClient
from app.llm.base import LLMClient
from app.llm.budget import LLMBudget
from app.llm.catalog import AVAILABLE_MODELS, LLMModel
from app.llm.gemini import GeminiClient

log = structlog.get_logger()


class LLMRegistry:
    def __init__(self, clients: dict[str, LLMClient]) -> None:
        self._clients = clients

    @classmethod
    def from_settings(cls, settings: Settings, budget: LLMBudget) -> "LLMRegistry":
        """Boot every provider whose API key is set. Skip the rest silently."""
        clients: dict[str, LLMClient] = {}

        if settings.ANTHROPIC_API_KEY:
            clients["anthropic"] = AnthropicClient(
                api_key=settings.ANTHROPIC_API_KEY,
                budget=budget,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )
        if settings.GEMINI_API_KEY:
            clients["gemini"] = GeminiClient(
                api_key=settings.GEMINI_API_KEY,
                budget=budget,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )

        log.info("llm.registry.booted", providers=sorted(clients.keys()))
        return cls(clients)

    def get(self, provider: str) -> LLMClient | None:
        return self._clients.get(provider)

    def available_models(self) -> list[LLMModel]:
        """Catalog filtered to providers that have a client booted."""
        return [m for m in AVAILABLE_MODELS if m.provider in self._clients]

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.aclose()
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                log.warning(
                    "llm.registry.aclose_failed",
                    provider=client.provider,
                    error=str(exc),
                )
