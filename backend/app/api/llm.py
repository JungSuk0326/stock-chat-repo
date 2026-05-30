"""GET /llm/models — model catalog filtered to providers with a configured key.

The front-end calls this once on load to populate the model dropdown. The
default model (settings.LLM_DEFAULT_*) is included only if its provider key
is set; if not, the UI falls back to whatever model the user picks from this
list.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.llm.registry import LLMRegistry
from app.schemas.chat import LLMModelInfo

router = APIRouter(prefix="/llm", tags=["llm"])


def _registry(request: Request) -> LLMRegistry:
    return request.app.state.llm_registry


@router.get("/models")
async def list_models(request: Request) -> dict[str, object]:
    registry = _registry(request)
    settings = get_settings()

    models = [
        LLMModelInfo(
            provider=m.provider,
            model_id=m.model_id,
            display_name=m.display_name,
            tier=m.tier,
            key=m.key,
        )
        for m in registry.available_models()
    ]

    # Default may be unavailable if its key isn't set — UI should detect that
    # and fall back to the first item in `models`.
    default_key = f"{settings.LLM_DEFAULT_PROVIDER}:{settings.LLM_DEFAULT_MODEL}"
    default_available = any(m.key == default_key for m in models)

    return {
        "models": [m.model_dump() for m in models],
        "default": {
            "provider": settings.LLM_DEFAULT_PROVIDER,
            "model_id": settings.LLM_DEFAULT_MODEL,
            "key": default_key,
            "available": default_available,
        },
    }
