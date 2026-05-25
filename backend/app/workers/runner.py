"""Worker process entry point.

Started by docker-compose as the `worker` service:
    command: uv run python -m app.workers.runner

This will host APScheduler with all worker jobs (price_poller, alert_runner, etc.).
For now it is a no-op placeholder that keeps the container alive so the deployment
topology (backend/worker separation per docs/risks-2026-05-21.md R4) is testable
end-to-end before real worker jobs land.
"""

import asyncio
import signal

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging

log = structlog.get_logger()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.ENVIRONMENT, settings.LOG_LEVEL)

    log.info(
        "worker.startup",
        environment=settings.ENVIRONMENT,
        enabled_markets=settings.enabled_markets,
        note="placeholder — real APScheduler jobs to follow",
    )

    # Wait for SIGINT/SIGTERM so docker compose stop is graceful.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("worker.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
