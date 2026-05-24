import structlog
from redis.asyncio import Redis, from_url

from app.core.config import get_settings

log = structlog.get_logger()
settings = get_settings()

redis_client: Redis = from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> Redis:
    """FastAPI dependency: yields the shared Redis client (connection-pooled)."""
    return redis_client


async def ping_redis() -> bool:
    """Returns True if Redis responds to PING, else False."""
    try:
        pong = await redis_client.ping()
        return pong is True
    except Exception as exc:
        log.warning("redis.ping_failed", error=str(exc))
        return False
