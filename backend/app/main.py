from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat as chat_api
from app.api import disclosures as disclosures_api
from app.api import instruments as instruments_api
from app.api import llm as llm_api
from app.api import prices as prices_api
from app.api import watchlist as watchlist_api
from app.api import ws_prices as ws_prices_api
from app.core.config import get_settings
from app.core.db import engine, ping_db
from app.core.logging import configure_logging
from app.core.redis_client import ping_redis, redis_client
from app.llm.budget import LLMBudget
from app.llm.registry import LLMRegistry
from app.services.market.kr import KrMarketAdapter

settings = get_settings()
configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "app.startup",
        environment=settings.ENVIRONMENT,
        log_level=settings.LOG_LEVEL,
        enabled_markets=settings.enabled_markets,
    )
    # Shared external-API client. Used by POST /watchlist for immediate backfill
    # so the user lands on a populated chart instead of waiting for the worker's
    # 30s reconcile cycle.
    app.state.kr_adapter = KrMarketAdapter()

    # Shared LLM registry. Budget enforces combined daily + monthly token caps
    # across all providers (R2). Registry boots clients only for providers
    # whose API key is set in .env.
    app.state.llm_budget = LLMBudget(
        redis=redis_client,
        daily_limit=settings.LLM_DAILY_TOKEN_CAP,
        monthly_limit=settings.LLM_MONTHLY_TOKEN_CAP,
    )
    app.state.llm_registry = LLMRegistry.from_settings(settings, app.state.llm_budget)
    yield
    log.info("app.shutdown")
    await app.state.llm_registry.aclose()
    await app.state.kr_adapter.aclose()
    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(
    title="Stock Advisor API",
    version="0.1.0",
    lifespan=lifespan,
)

# Local frontend (Next.js dev server) is the only browser origin for now.
# Cloudflare Tunnel deploy will pass through that domain via reverse proxy,
# not directly to the browser → no extra CORS needed at deploy time.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["*"],
)

app.include_router(prices_api.router)
app.include_router(ws_prices_api.router)
app.include_router(instruments_api.router)
app.include_router(watchlist_api.router)
app.include_router(chat_api.router)
app.include_router(llm_api.router)
app.include_router(disclosures_api.router)


@app.get("/health")
async def health(response: Response) -> dict[str, object]:
    db_ok = await ping_db()
    redis_ok = await ping_redis()
    all_ok = db_ok and redis_ok

    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if all_ok else "degraded",
        "environment": settings.ENVIRONMENT,
        "enabled_markets": settings.enabled_markets,
        "checks": {
            "db": "ok" if db_ok else "fail",
            "redis": "ok" if redis_ok else "fail",
        },
    }
