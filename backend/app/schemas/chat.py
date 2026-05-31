from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    """One message in the chat history."""

    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """Chat request.

    `session_id` is optional. If omitted, the backend creates a new session
    for (user, instrument) and returns its id in the response. The client
    persists the id and sends it back on subsequent turns.

    `history` is no longer required from the client — the backend replays
    it from `chat_messages` for the session. Clients MAY still send a
    history for ephemeral, non-persisted exchanges (then session_id must
    be omitted), in which case we DON'T persist anything.

    `provider` / `model` are optional. When omitted, the backend falls back
    to LLM_DEFAULT_PROVIDER / LLM_DEFAULT_MODEL from settings.
    """

    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    question: str = Field(..., min_length=1, max_length=4000)
    session_id: int | None = Field(default=None)
    history: list[ChatTurn] | None = Field(default=None)
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    answer: str
    instrument: str  # canonical id
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    context_preview: str  # for debugging / transparency
    session_id: int | None = None  # null only for ephemeral mode


class LLMModelInfo(BaseModel):
    """Catalog entry exposed via GET /llm/models. Only providers with a
    configured API key make it into the response."""

    provider: str
    model_id: str
    display_name: str
    tier: str
    key: str  # "{provider}:{model_id}" — stable id for UI / localStorage
