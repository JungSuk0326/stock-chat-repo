"""WebSocket endpoint that fans realtime price ticks from Redis to the browser.

Per `docs/risks-2026-05-21.md` and CLAUDE.md "실시간 시세 흐름":
- Path: GET /ws/prices/{exchange}/{symbol}
- Channel: ticks.{exchange}.{symbol} (Redis Pub/Sub)
- Cache: price:{exchange}:{symbol} (Redis SET, used for the initial frame so a
  client connecting between ticks sees the last known price immediately).

Backpressure / lifecycle:
- One Redis pubsub connection per WebSocket (kept open for the connection's lifetime).
- Two concurrent tasks: forward (Redis → ws) and watch (ws.receive → detect disconnect).
- asyncio.wait(FIRST_COMPLETED) ends both as soon as either side closes.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.redis_client import redis_client
from app.models import Instrument

log = structlog.get_logger()

router = APIRouter(tags=["prices"])

# WebSocket close codes 4xxx are application-defined (RFC 6455). 4404 = "not found".
WS_CLOSE_NOT_FOUND = 4404


def _channel(exchange: str, symbol: str) -> str:
    return f"ticks.{exchange}.{symbol}"


def _cache_key(exchange: str, symbol: str) -> str:
    return f"price:{exchange}:{symbol}"


async def _instrument_exists(exchange: str, symbol: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Instrument.id).where(
                Instrument.exchange == exchange,
                Instrument.symbol == symbol,
            )
        )
    return result.scalar_one_or_none() is not None


@router.websocket("/ws/prices/{exchange}/{symbol}")
async def ws_prices(
    websocket: WebSocket,
    exchange: str,
    symbol: str,
) -> None:
    exchange_norm = exchange.upper().strip()
    symbol_norm = symbol.strip()

    # Accept first so we can return a proper WebSocket close frame with our
    # application-defined code. Closing before accept produces an HTTP 403,
    # which strips the close code from the client's perspective.
    await websocket.accept()

    if not await _instrument_exists(exchange_norm, symbol_norm):
        await websocket.close(
            code=WS_CLOSE_NOT_FOUND,
            reason=f"Instrument not found: {exchange_norm}:{symbol_norm}",
        )
        return
    log.info("ws_prices.connected", exchange=exchange_norm, symbol=symbol_norm)

    pubsub = redis_client.pubsub()
    channel = _channel(exchange_norm, symbol_norm)
    await pubsub.subscribe(channel)

    try:
        # Initial frame: last cached tick (avoids up-to-2s "empty chart" gap)
        cached = await redis_client.get(_cache_key(exchange_norm, symbol_norm))
        if cached:
            await websocket.send_text(cached)

        forward_task = asyncio.create_task(
            _forward_ticks(pubsub, websocket),
            name=f"ws_forward_{exchange_norm}_{symbol_norm}",
        )
        watch_task = asyncio.create_task(
            _watch_disconnect(websocket),
            name=f"ws_watch_{exchange_norm}_{symbol_norm}",
        )

        done, pending = await asyncio.wait(
            {forward_task, watch_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        # Surface non-cancellation exceptions (e.g., Redis error) for logging.
        for task in done:
            if not task.cancelled():
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    log.warning(
                        "ws_prices.task_error",
                        exchange=exchange_norm,
                        symbol=symbol_norm,
                        error=str(exc),
                    )
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001 — cleanup best-effort
            log.warning("ws_prices.pubsub_close_error", error=str(exc))
        log.info("ws_prices.disconnected", exchange=exchange_norm, symbol=symbol_norm)


async def _forward_ticks(pubsub, websocket: WebSocket) -> None:
    """Pump Redis Pub/Sub messages to the WebSocket as text frames."""
    async for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        data = message["data"]
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        await websocket.send_text(data)


async def _watch_disconnect(websocket: WebSocket) -> None:
    """Detect client disconnect. Browsers in our protocol never send to us,
    so any receive_text() either blocks forever or raises WebSocketDisconnect
    when the client closes.
    """
    while True:
        await websocket.receive_text()
