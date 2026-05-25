from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api import prices as prices_api
from app.api import ws_prices as ws_prices_api
from app.core.config import get_settings
from app.core.db import engine, ping_db
from app.core.logging import configure_logging
from app.core.redis_client import ping_redis, redis_client

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
    yield
    log.info("app.shutdown")
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
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(prices_api.router)
app.include_router(ws_prices_api.router)


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
