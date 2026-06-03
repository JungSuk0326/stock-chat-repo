import os
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

    # KRX login (data.krx.co.kr free account). pykrx reads these env vars
    # directly via `build_krx_session()` — the only thing this declaration
    # buys us is dotenv loading + a single place to document the dependency.
    # Required for investor-flow detail (사모/연기금/투신 etc.) endpoints
    # which KRX gated behind login. Used by:
    #   - market_investor_flow_daily worker (pykrx 세분류 적재)
    #   - discover_by_investor_flow LLM 도구 (자연어 발굴)
    KRX_ID: str = ""
    KRX_PW: str = ""

    # KRX OpenAPI key (openapi.krx.co.kr) — official REST API for stock daily
    # OHLCV + instrument basic info. Separate account from KRX_ID/PW.
    # When set, the daily EOD price sync routes through this instead of
    # pykrx; backfill still uses pykrx because the per-date paginated shape
    # of the OpenAPI is wasteful for "fetch one symbol's 1-year history".
    KRX_OPENAPI_KEY: str = ""

    # Alerts
    # "log" routes fires to structlog (always works, dev default).
    # "telegram" requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID; falls back
    # to "log" at runtime if either is missing.
    ALERT_CHANNEL: str = "log"

    # DB backup (R5). Daily pg_dump → gzipped file under BACKUP_DIR.
    # In docker-compose the worker mounts ./backups → /backups; locally
    # the relative path resolves against the worker's CWD.
    BACKUP_DIR: str = "/backups"
    BACKUP_RETENTION_DAYS: int = 14

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


# Settings → os.environ keys that need to be visible to libraries that
# bypass pydantic-settings entirely. pykrx (KRX_ID/KRX_PW) reads via
# os.getenv at session-build time.
_EXPORT_TO_OS_ENVIRON = ("KRX_ID", "KRX_PW")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for key in _EXPORT_TO_OS_ENVIRON:
        value = getattr(s, key, "")
        if value:
            os.environ.setdefault(key, value)
    return s
