from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Runtime
    ENVIRONMENT: Environment = "dev"
    LOG_LEVEL: LogLevel = "INFO"

    # Markets — comma-separated. Phase 1: "KR" only. Phase 2: "KR,US".
    ENABLED_MARKETS: str = "KR"

    # Dev escape hatch: ignore market-hours check in the price poller so it polls
    # 24/7. Set to true only for local smoke testing. Never enable in prod —
    # the Naver Mobile API serves stale closes off-hours.
    ALLOW_OFF_HOURS_POLLING: bool = False

    # Personal auth (single-user)
    AUTH_PASSWORD: str = "change-me"

    # Database / Cache (defaults point at local docker-compose services)
    DATABASE_URL: str = "postgresql+asyncpg://stock:stock@localhost:5432/stock_advisor"
    REDIS_URL: str = "redis://localhost:6379/0"

    # External APIs — empty string means "not configured"
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    DART_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # LLM
    # Default provider/model when the client doesn't specify one. Catalog
    # filters by which keys are set (no key → not offered to the UI).
    LLM_DEFAULT_PROVIDER: str = "gemini"
    LLM_DEFAULT_MODEL: str = "gemini-2.5-pro"
    LLM_DAILY_TOKEN_CAP: int = 100_000      # combined across all providers
    LLM_MONTHLY_TOKEN_CAP: int = 2_000_000  # combined across all providers
    LLM_MAX_OUTPUT_TOKENS: int = 2048       # cap per response

    @property
    def enabled_markets(self) -> list[str]:
        return [m.strip().upper() for m in self.ENABLED_MARKETS.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
