"""Alert evaluation + dispatch service.

Called once per minute by the worker (`tick_alert_runner` in this module).
On each tick:

  1. Fetch every enabled rule
  2. For each rule: skip if in cooldown OR market_hours_only & market closed
  3. Resolve current price (Redis snapshot first, falls back to last 1d EOD)
  4. Resolve prev close (last 1d bar's close) — needed for pct_change rules
  5. Evaluate the rule's condition
  6. If fired: send via configured channel + UPDATE last_triggered_at +
     INSERT alert_events row

The runner is single-process (singleton worker) so we don't bother with a
Redis lock around evaluation — APScheduler `max_instances=1` already
prevents overlap.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from redis.asyncio import Redis
from sqlalchemy import desc, select, update

from app.core.db import SessionLocal
from app.models import AlertEvent, AlertRule, Instrument, Price
from app.services.alert_channels.base import AlertChannel, DeliveryResult

log = structlog.get_logger()

_KST = ZoneInfo("Asia/Seoul")

# Supported condition_type values. Adding a new one means extending the
# match block in _evaluate(). The schema is wide enough already.
CONDITION_TYPES = frozenset(
    {
        "price_above",
        "price_below",
        "pct_change_above",
        "pct_change_below",
    }
)


def _is_kr_market_open(now_utc: datetime) -> bool:
    """KRX regular session: 09:00–15:30 KST, Mon–Fri.

    Holiday calendar omitted for Phase 1 — pandas_market_calendars
    integration is tracked under R15 in the risks doc.
    """
    local = now_utc.astimezone(_KST)
    if local.weekday() >= 5:
        return False
    return time(9, 0) <= local.time() <= time(15, 30)


def _market_open_for(exchange: str, now_utc: datetime) -> bool:
    """Phase 1 supports KR only. Other exchanges are treated as 'always
    open' so US/JP rules don't get accidentally suppressed."""
    if exchange == "KR":
        return _is_kr_market_open(now_utc)
    return True


def _in_cooldown(rule: AlertRule, now_utc: datetime) -> bool:
    if rule.last_triggered_at is None:
        return False
    return rule.last_triggered_at + timedelta(minutes=rule.cooldown_minutes) > now_utc


async def _read_current_price(
    redis: Redis, exchange: str, symbol: str
) -> Decimal | None:
    """Redis cache first (live tick). Caller falls back to last EOD."""
    raw = await redis.get(f"price:{exchange}:{symbol}")
    if not raw:
        return None
    try:
        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        return Decimal(str(data["close"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts.cache_parse_failed", error=str(exc))
        return None


async def _read_last_two_eod(
    session, instrument_id: int
) -> tuple[Decimal | None, Decimal | None]:
    """Return (latest_close, previous_close) from `prices` daily bars.

    Used as price fallback AND as the reference for pct_change rules.
    """
    stmt = (
        select(Price.close)
        .where(Price.instrument_id == instrument_id, Price.interval == "1d")
        .order_by(desc(Price.time))
        .limit(2)
    )
    rows = (await session.execute(stmt)).scalars().all()
    latest = rows[0] if rows else None
    prev = rows[1] if len(rows) >= 2 else None
    return latest, prev


def _evaluate(
    *,
    condition_type: str,
    threshold: Decimal,
    current_price: Decimal,
    prev_close: Decimal | None,
) -> tuple[bool, Decimal | None]:
    """Pure check — returns (should_fire, triggered_value).

    triggered_value is the metric that actually crossed the threshold:
      - price_*  → current_price
      - pct_*    → the percentage value
    """
    if condition_type == "price_above":
        return current_price >= threshold, current_price
    if condition_type == "price_below":
        return current_price <= threshold, current_price
    if condition_type in ("pct_change_above", "pct_change_below"):
        if prev_close is None or prev_close == 0:
            return False, None
        pct = (current_price - prev_close) / prev_close * Decimal(100)
        if condition_type == "pct_change_above":
            return pct >= threshold, pct
        return pct <= threshold, pct
    log.warning("alerts.unknown_condition", condition_type=condition_type)
    return False, None


def _format_message(
    rule: AlertRule, instrument: Instrument, triggered_value: Decimal
) -> tuple[str, str]:
    """Build (title, body) for a fired rule. Channel implementations may
    use either or both depending on the medium."""
    label = instrument.name or f"{instrument.exchange}:{instrument.symbol}"
    rule_name = rule.name or "이름 없는 규칙"

    if rule.condition_type == "price_above":
        msg = f"{label} 현재가 {int(triggered_value):,}원 (≥ {int(rule.threshold):,}원)"
    elif rule.condition_type == "price_below":
        msg = f"{label} 현재가 {int(triggered_value):,}원 (≤ {int(rule.threshold):,}원)"
    elif rule.condition_type == "pct_change_above":
        msg = f"{label} 전일대비 {triggered_value:+.2f}% (≥ {rule.threshold:+.2f}%)"
    elif rule.condition_type == "pct_change_below":
        msg = f"{label} 전일대비 {triggered_value:+.2f}% (≤ {rule.threshold:+.2f}%)"
    else:
        msg = f"{label} (조건 미상)"

    title = f"[알림] {rule_name}"
    body = msg
    return title, body


async def tick_alert_runner(redis: Redis, channel: AlertChannel) -> int:
    """One evaluation pass over all enabled rules. Returns the number of
    rules that fired this tick. Called from APScheduler every minute.

    Idempotent vis-à-vis cooldown: a rule whose condition is true but
    still within its cooldown window is silently skipped.
    """
    now = datetime.now(timezone.utc)
    fired_count = 0

    async with SessionLocal() as session:
        rules = list(
            (
                await session.execute(
                    select(AlertRule).where(AlertRule.enabled.is_(True))
                )
            )
            .scalars()
            .all()
        )

        for rule in rules:
            if _in_cooldown(rule, now):
                continue

            instrument = await session.get(Instrument, rule.instrument_id)
            if instrument is None:
                log.warning(
                    "alerts.no_instrument",
                    rule_id=rule.id,
                    instrument_id=rule.instrument_id,
                )
                continue

            if rule.market_hours_only and not _market_open_for(
                instrument.exchange, now
            ):
                continue

            # Price resolution: Redis live → DB EOD fallback
            current_price = await _read_current_price(
                redis, instrument.exchange, instrument.symbol
            )
            latest_eod, prev_eod = await _read_last_two_eod(session, instrument.id)
            if current_price is None:
                current_price = latest_eod
            if current_price is None:
                # No price data at all → nothing to evaluate. Will be
                # available next tick once the price worker populates it.
                continue

            should_fire, triggered = _evaluate(
                condition_type=rule.condition_type,
                threshold=rule.threshold,
                current_price=current_price,
                prev_close=prev_eod,
            )
            if not should_fire or triggered is None:
                continue

            title, body = _format_message(rule, instrument, triggered)

            try:
                result = await channel.send(title=title, body=body)
            except Exception as exc:  # noqa: BLE001
                result = DeliveryResult(status="failed", error=str(exc)[:500])
                log.exception("alerts.channel_failed", rule_id=rule.id)

            # Bump last_triggered_at + record event regardless of delivery
            # outcome so retry/duplication is bounded by cooldown.
            await session.execute(
                update(AlertRule)
                .where(AlertRule.id == rule.id)
                .values(last_triggered_at=now)
            )
            event = AlertEvent(
                rule_id=rule.id,
                fired_at=now,
                triggered_value=triggered,
                channel=channel.name,
                delivery_status=result.status,
                error=result.error,
            )
            session.add(event)
            await session.commit()

            log.info(
                "alerts.fired",
                rule_id=rule.id,
                instrument=f"{instrument.exchange}:{instrument.symbol}",
                condition=rule.condition_type,
                triggered=str(triggered),
                channel=channel.name,
                status=event.delivery_status,
            )
            fired_count += 1

    return fired_count
