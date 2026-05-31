"""TelegramChannel — Telegram Bot API sender.

Single user app, so bot_token + chat_id pair lives in .env. The bot
must already exist (created via @BotFather) and the user must have
sent at least one message to the bot OR the chat_id must be that of
a group/channel the bot is part of.

We don't poll for updates or handle inbound messages — strictly
outbound notifications.
"""

from __future__ import annotations

import httpx
import structlog

from app.services.alert_channels.base import AlertChannel, DeliveryResult

log = structlog.get_logger()

_API_BASE = "https://api.telegram.org"


class TelegramChannel(AlertChannel):
    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError(
                "TelegramChannel needs both bot_token and chat_id "
                "(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
            )
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def send(self, *, title: str, body: str) -> DeliveryResult:
        # Telegram supports a few parse_modes; we use HTML because it's
        # easiest to escape and the bold/code spans we use are minimal.
        text = f"<b>{_html_escape(title)}</b>\n{_html_escape(body)}"
        url = f"{_API_BASE}/bot{self._bot_token}/sendMessage"
        try:
            resp = await self._http.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(status="failed", error=str(exc)[:500])

        if resp.status_code != 200:
            return DeliveryResult(
                status="failed",
                error=f"HTTP {resp.status_code}: {resp.text[:300]}",
            )

        try:
            data = resp.json()
        except ValueError as exc:
            return DeliveryResult(
                status="failed", error=f"non-JSON response: {exc}"
            )

        if not data.get("ok"):
            return DeliveryResult(
                status="failed",
                error=str(data.get("description", "unknown"))[:300],
            )

        return DeliveryResult(status="sent")

    async def aclose(self) -> None:
        await self._http.aclose()


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
