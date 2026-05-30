"""Static catalog of LLM models the app may offer.

The registry filters this list at boot by which API keys are configured. The
front-end fetches the filtered list via GET /llm/models to populate its
model dropdown.

To add a new model later, append an entry here. To remove one, delete the
entry. No client code change needed beyond having that provider's wrapper
installed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMModel:
    provider: str       # matches LLMClient.provider ("anthropic" | "gemini" | ...)
    model_id: str       # exact id passed to the provider SDK
    display_name: str   # shown in UI dropdown
    tier: str           # "premium" | "standard" | "lite" — UX hint

    @property
    def key(self) -> str:
        """Stable identifier used in URLs / localStorage."""
        return f"{self.provider}:{self.model_id}"


# Order roughly: premium → lite, by provider. The UI groups by provider.
AVAILABLE_MODELS: tuple[LLMModel, ...] = (
    # --- Anthropic ---
    LLMModel(
        provider="anthropic",
        model_id="claude-opus-4-7",
        display_name="Claude Opus 4.7",
        tier="premium",
    ),
    LLMModel(
        provider="anthropic",
        model_id="claude-haiku-4-5",
        display_name="Claude Haiku 4.5",
        tier="lite",
    ),
    # --- Gemini ---
    LLMModel(
        provider="gemini",
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        tier="premium",
    ),
    LLMModel(
        provider="gemini",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        tier="standard",
    ),
)


def get_model(provider: str, model_id: str) -> LLMModel | None:
    """Lookup by (provider, model_id). Used to validate /chat requests."""
    for m in AVAILABLE_MODELS:
        if m.provider == provider and m.model_id == model_id:
            return m
    return None
