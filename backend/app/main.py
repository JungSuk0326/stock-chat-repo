from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging

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


app = FastAPI(
    title="Stock Advisor API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "enabled_markets": settings.enabled_markets,
    }
