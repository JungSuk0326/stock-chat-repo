import logging
import sys

import structlog
from structlog.typing import Processor

from app.core.config import Environment, LogLevel


def configure_logging(environment: Environment, log_level: LogLevel) -> None:
    """Configure structlog.

    - dev: colored console output, easy to read
    - prod: JSON output, suitable for log aggregation (Loki, Elastic, etc.)
    """
    level = getattr(logging, log_level)

    # stdlib logging: route to stdout at the configured level.
    # Required so libraries that use stdlib logging (uvicorn, sqlalchemy) honor LOG_LEVEL.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if environment == "dev":
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
