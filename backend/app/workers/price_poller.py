"""Realtime price poller for KR symbols.

Every tick (default: 2s) during market hours:
  1. fetch realtime snapshot from the market adapter
  2. SET Redis cache  `price:{EX}:{SYM}` with 60s TTL
  3. PUBLISH Redis    `ticks.{EX}.{SYM}` for WebSocket fan-out
  4. buffer the tick; at minute boundaries flush an OHLCV row into `prices`

One PricePoller instance owns one (exchange, symbol). Multiple symbols = multiple
instances scheduled separately.

Singleton safety: each tick acquires a short Redis lock so two worker containers
do not double-publish. Holiday handling is deferred (see R15 in docs/risks).
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
from app.services.market.calendar import kr_market_open
from app.services.market.kr import KrMarketAdapter

log = structlog.get_logger()


def _redis_cache_key(exchange: str, symbol: str) -> str:
    return f"price:{exchange}:{symbol}"


def _redis_channel(exchange: str, symbol: str) -> str:
    return f"ticks.{exchange}.{symbol}"


def _redis_lock_key(exchange: str, symbol: str) -> str:
    return f"lock:poller:{exchange}:{symbol}"


@dataclass
class _MinuteBuffer:
    """Accumulates ticks within the same UTC minute for 1m bar aggregation."""

    minute_start: datetime  # inclusive, second/microsecond zeroed
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
    """One poller per (exchange, symbol). Owns its in-memory minute buffer."""

    exchange: str
    symbol: str
    adapter: KrMarketAdapter
    redis: Redis
    lock_ttl_seconds: int = 5
    cache_ttl_seconds: int = 60

    _instrument_id: int | None = None
    _buffer: _MinuteBuffer | None = field(default=None, init=False)

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
        """One scheduler tick. Skips if market closed, singleton lock held, or fetch fails."""
        if not (kr_market_open() or get_settings().ALLOW_OFF_HOURS_POLLING):
            return

        instrument_id = await self._resolve_instrument_id()
        if instrument_id is None:
            return

        # Singleton lock — if another worker is in the middle of a tick for this
        # symbol, skip. Short TTL so a crashed worker doesn't block forever.
        lock_acquired = await self.redis.set(
            _redis_lock_key(self.exchange, self.symbol),
            "1",
            nx=True,
            ex=self.lock_ttl_seconds,
        )
        if not lock_acquired:
            return

        try:
            tick_data = await self.adapter.fetch_realtime_price(self.symbol)
            if tick_data is None:
                return

            payload = {
                "exchange": self.exchange,
                "symbol": self.symbol,
                "ts": tick_data.ts.isoformat(),
                "close": str(tick_data.close),
                "volume_cum": tick_data.volume_cum,
            }
            payload_json = json.dumps(payload)

            # (1) cache + (2) publish — fire-and-forget within this lock.
            await self.redis.set(
                _redis_cache_key(self.exchange, self.symbol),
                payload_json,
                ex=self.cache_ttl_seconds,
            )
            await self.redis.publish(
                _redis_channel(self.exchange, self.symbol),
                payload_json,
            )

            # (3) buffer + flush at minute boundaries
            await self._handle_minute_aggregation(instrument_id, tick_data)
        finally:
            await self.redis.delete(_redis_lock_key(self.exchange, self.symbol))

    async def _handle_minute_aggregation(
        self,
        instrument_id: int,
        tick: RealtimePrice,
    ) -> None:
        minute_start = tick.ts.replace(second=0, microsecond=0).astimezone(timezone.utc)

        if self._buffer is None:
            self._buffer = _MinuteBuffer.from_tick(minute_start, tick)
            return

        if minute_start == self._buffer.minute_start:
            self._buffer.update(tick)
            return

        # Boundary crossed → flush the previous minute and start a new buffer.
        await self._flush_buffer(instrument_id, self._buffer)
        self._buffer = _MinuteBuffer.from_tick(minute_start, tick)

    async def _flush_buffer(
        self,
        instrument_id: int,
        buffer: _MinuteBuffer,
    ) -> None:
        async with SessionLocal() as session:
            stmt = pg_insert(Price).values(
                instrument_id=instrument_id,
                interval="1m",
                time=buffer.minute_start,
                open=buffer.first_close,
                high=buffer.high,
                low=buffer.low,
                close=buffer.last_close,
                volume=buffer.volume,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "interval", "time"],
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
            minute=buffer.minute_start.isoformat(),
            open=str(buffer.first_close),
            close=str(buffer.last_close),
            high=str(buffer.high),
            low=str(buffer.low),
            volume=buffer.volume,
        )
