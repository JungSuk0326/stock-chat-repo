from collections.abc import AsyncIterator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

log = structlog.get_logger()
settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields one AsyncSession per request."""
    async with SessionLocal() as session:
        yield session


async def ping_db() -> bool:
    """Returns True if the database responds to SELECT 1, else False."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("db.ping_failed", error=str(exc))
        return False
