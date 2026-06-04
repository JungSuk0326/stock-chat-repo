"""Realtime price poller for KR symbols — KRX + NXT.

Every tick (default 5s) during the 08:00-20:00 KST polling window:
  1. fetch a single Naver polling snapshot — returns BOTH KRX and NXT legs
  2. for each leg: SET Redis cache `price:{EX}:{SYM}:{VENUE}` (TTL 60s)
  3. for each leg: PUBLISH on `ticks.{EX}.{SYM}` with `venue` in payload
  4. for each leg: accumulate into a per-venue minute buffer; flush at the
     minute boundary into `prices` (with venue column)

One PricePoller instance owns one (exchange, symbol). It tracks one minute
buffer per venue so KRX and NXT 1m bars stay independent (they often have
different highs/lows even at the same minute).

Singleton safety: each tick acquires a short Redis lock so two worker
containers don't double-publish. Holiday handling deferred (R15).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Instrument, Price
from app.services.market.base import RealtimePrice
from app.services.market.calendar import kr_polling_window_open
from app.services.market.naver_polling import NaverPollingAdapter

log = structlog.get_logger()


# Cache: per-venue so the WebSocket initial frame can deliver both legs.
def _redis_cache_key(exchange: str, symbol: str, venue: str) -> str:
    return f"price:{exchange}:{symbol}:{venue}"


# Channel: one per symbol; payload's `venue` field discriminates KRX vs NXT.
# Keeping a single channel avoids pattern-subscribe gymnastics in the WS
# handler and matches the frontend's "one chart, multiple tabs" model.
def _redis_channel(exchange: str, symbol: str) -> str:
    return f"ticks.{exchange}.{symbol}"


# Lock: per-symbol (not per-venue) — the whole tick is one HTTP call so
# splitting the lock would be pointless.
def _redis_lock_key(exchange: str, symbol: str) -> str:
    return f"lock:poller:{exchange}:{symbol}"


@dataclass
class _MinuteBuffer:
    """Accumulates ticks within the same UTC minute for 1m bar aggregation."""

    minute_start: datetime
    first_close: Decimal
    high: Decimal
    low: Decimal
    last_close: Decimal
    first_volume_cum: int
    last_volume_cum: int

    @classmethod
    def from_tick(cls, minute_start: datetime, tick: RealtimePrice) -> "_MinuteBuffer":
        return cls(
            minute_start=minute_start,
            first_close=tick.close,
            high=tick.close,
            low=tick.close,
            last_close=tick.close,
            first_volume_cum=tick.volume_cum,
            last_volume_cum=tick.volume_cum,
        )

    def update(self, tick: RealtimePrice) -> None:
        if tick.close > self.high:
            self.high = tick.close
        if tick.close < self.low:
            self.low = tick.close
        self.last_close = tick.close
        self.last_volume_cum = tick.volume_cum

    @property
    def volume(self) -> int:
        return max(self.last_volume_cum - self.first_volume_cum, 0)


@dataclass
class PricePoller:
    """One poller per (exchange, symbol). Owns one minute buffer per venue."""

    exchange: str
    symbol: str
    adapter: NaverPollingAdapter
    redis: Redis
    lock_ttl_seconds: int = 10
    cache_ttl_seconds: int = 60

    _instrument_id: int | None = None
    # venue → minute buffer. Each venue maintains its own independent 1m bars.
    _buffers: dict[str, _MinuteBuffer] = field(default_factory=dict, init=False)

    async def _resolve_instrument_id(self) -> int | None:
        if self._instrument_id is not None:
            return self._instrument_id
        async with SessionLocal() as session:
            result = await session.execute(
                select(Instrument).where(
                    Instrument.exchange == self.exchange,
                    Instrument.symbol == self.symbol,
                )
            )
            instrument = result.scalar_one_or_none()
        if instrument is None:
            log.error(
                "poller.instrument_not_found",
                exchange=self.exchange,
                symbol=self.symbol,
            )
            return None
        self._instrument_id = instrument.id
        return instrument.id

    async def tick(self) -> None:
        """One scheduler tick — fetches both venues, fans out per leg."""
        if not (
            kr_polling_window_open() or get_settings().ALLOW_OFF_HOURS_POLLING
        ):
            return

        instrument_id = await self._resolve_instrument_id()
        if instrument_id is None:
            return

        # Singleton lock so two workers don't both poll + publish.
        lock_acquired = await self.redis.set(
            _redis_lock_key(self.exchange, self.symbol),
            "1",
            nx=True,
            ex=self.lock_ttl_seconds,
        )
        if not lock_acquired:
            return

        try:
            ticks = await self.adapter.fetch_realtime_prices(self.symbol)
            if not ticks:
                return

            for tick_data in ticks:
                await self._publish_tick(tick_data)
                await self._handle_minute_aggregation(instrument_id, tick_data)
        finally:
            await self.redis.delete(_redis_lock_key(self.exchange, self.symbol))

    async def _publish_tick(self, tick: RealtimePrice) -> None:
        """Cache + publish one venue's tick. Payload carries `venue` so the
        single ticks.* channel can mux both legs into one stream."""
        payload = {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "venue": tick.venue,
            "ts": tick.ts.isoformat(),
            "close": str(tick.close),
            "volume_cum": tick.volume_cum,
        }
        payload_json = json.dumps(payload)

        await self.redis.set(
            _redis_cache_key(self.exchange, self.symbol, tick.venue),
            payload_json,
            ex=self.cache_ttl_seconds,
        )
        await self.redis.publish(
            _redis_channel(self.exchange, self.symbol),
            payload_json,
        )

    async def _handle_minute_aggregation(
        self,
        instrument_id: int,
        tick: RealtimePrice,
    ) -> None:
        minute_start = tick.ts.replace(second=0, microsecond=0).astimezone(timezone.utc)
        buffer = self._buffers.get(tick.venue)

        if buffer is None:
            self._buffers[tick.venue] = _MinuteBuffer.from_tick(minute_start, tick)
            return

        if minute_start == buffer.minute_start:
            buffer.update(tick)
            return

        # Boundary crossed → flush the previous minute, start a new buffer.
        await self._flush_buffer(instrument_id, tick.venue, buffer)
        self._buffers[tick.venue] = _MinuteBuffer.from_tick(minute_start, tick)

    async def _flush_buffer(
        self,
        instrument_id: int,
        venue: str,
        buffer: _MinuteBuffer,
    ) -> None:
        async with SessionLocal() as session:
            stmt = pg_insert(Price).values(
                instrument_id=instrument_id,
                interval="1m",
                venue=venue,
                time=buffer.minute_start,
                open=buffer.first_close,
                high=buffer.high,
                low=buffer.low,
                close=buffer.last_close,
                volume=buffer.volume,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "interval", "venue", "time"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                },
            )
            await session.execute(stmt)
            await session.commit()

        log.info(
            "poller.bar_flushed",
            exchange=self.exchange,
            symbol=self.symbol,
            venue=venue,
            minute=buffer.minute_start.isoformat(),
            close=str(buffer.last_close),
            volume=buffer.volume,
        )
