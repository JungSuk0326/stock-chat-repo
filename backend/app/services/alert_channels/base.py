"""Alert delivery channel abstraction.

Phase B (Top4) ships a LogChannel; Phase C adds TelegramChannel. The runner
picks one channel at boot based on settings — the alert_events table
records which channel was actually used for each delivery, so the column
doubles as audit trail.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DeliveryResult:
    """Outcome of a single channel.send() call.

    `status` matches the `delivery_status` column on `alert_events`:
      - "sent": delivered cleanly
      - "failed": channel raised or returned an error
      - "skipped": channel intentionally did not deliver (e.g. dry-run)
    `error` is populated on "failed".
    """

    status: str
    error: str | None = None


class AlertChannel(ABC):
    """Async sink for alert messages."""

    #: Channel identifier, persisted in alert_events.channel
    name: str

    @abstractmethod
    async def send(self, *, title: str, body: str) -> DeliveryResult:
        """Deliver one alert. `title` is a short summary, `body` is the
        formatted full message. Implementations decide how to combine."""
        ...

    async def aclose(self) -> None:
        """Optional cleanup. Default no-op."""
        return None
