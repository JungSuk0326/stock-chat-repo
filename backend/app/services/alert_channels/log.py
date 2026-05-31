"""LogChannel — emits an INFO log line per alert.

Used in dev / before TELEGRAM_BOT_TOKEN is configured. Also acts as the
fallback when settings.ALERT_CHANNEL is unrecognized, so the runner never
crashes on misconfiguration.
"""

from __future__ import annotations

import structlog

from app.services.alert_channels.base import AlertChannel, DeliveryResult

log = structlog.get_logger()


class LogChannel(AlertChannel):
    name = "log"

    async def send(self, *, title: str, body: str) -> DeliveryResult:
        log.info("alert.fired", title=title, body=body)
        return DeliveryResult(status="sent")
