"""Worker job heartbeats — Redis-backed liveness tracking.

Each scheduled job that we care about wraps its run with `with_heartbeat()`.
On success we update `worker:hb:{job}` with the run timestamp + ok flag;
on failure we capture the error message too. `/health` reads these keys
to surface "this job hasn't succeeded in a while" before the user notices.

Per-symbol pollers (price_poller_*) are NOT tracked individually — too
noisy. The reconcile job (`watchlist_sync`) covers the polling layer
indirectly: if reconcile is healthy, pollers are being kept alive.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()

T = TypeVar("T")


# Job name → max acceptable seconds since last_ok before we flag as stale.
# Allow ~3-5× the nominal interval so transient slowness doesn't false-alarm.
HEARTBEAT_EXPECTATIONS: dict[str, int] = {
    # interval jobs
    "watchlist_sync": 120,        # nominal 30s
    "disclosure_poll": 300,       # nominal 60s
    "alert_runner": 300,          # nominal 60s
    "news_poll": 1200,            # nominal 5min, stale at 20min
    # daily crons (allow 1h grace past the 24h window)
    "eod_sync_daily": 25 * 3600,
    "instruments_sync_daily": 25 * 3600,
    "dart_corp_code_sync_daily": 25 * 3600,
    "backup_daily": 25 * 3600,
    "fundamentals_refresh_daily": 25 * 3600,
    "screener_run_daily": 25 * 3600,
    "investor_flow_sync_daily": 25 * 3600,
    "market_investor_flow_daily": 25 * 3600,
    "nxt_eod_daily": 25 * 3600,
}


def _key(job: str) -> str:
    return f"worker:hb:{job}"


@dataclass
class Heartbeat:
    job: str
    last_run_ts: float | None  # epoch seconds, last attempt
    last_ok_ts: float | None   # epoch seconds, last success
    ok: bool                   # last attempt result
    error: str | None          # last failure detail, capped


async def record(redis: Redis, job: str, *, ok: bool, error: str | None = None) -> None:
    """Update the heartbeat for `job`. Always sets last_run_ts; updates
    last_ok_ts only when `ok=True`. Carries the previous last_ok_ts
    forward across failures so /health can tell "intermittently failing
    but recovered" from "down for hours"."""
    now = time.time()
    existing_raw = await redis.get(_key(job))
    last_ok_ts: float | None = now if ok else None
    if existing_raw and not ok:
        try:
            existing = json.loads(
                existing_raw if isinstance(existing_raw, str) else existing_raw.decode()
            )
            last_ok_ts = existing.get("last_ok_ts")
        except Exception:  # noqa: BLE001 — best effort, fall through to None
            last_ok_ts = None
    payload = {
        "last_run_ts": now,
        "last_ok_ts": last_ok_ts,
        "ok": ok,
        "error": (error or "")[:512] if error else None,
    }
    # Long TTL so a long-dead worker still shows up as "stale" in /health
    # for several days, not silently missing.
    await redis.set(_key(job), json.dumps(payload), ex=14 * 24 * 3600)


async def read_all(redis: Redis) -> list[Heartbeat]:
    """Snapshot every tracked job. Jobs we know about but have never
    written a heartbeat for show up as ok=False, last_*_ts=None."""
    out: list[Heartbeat] = []
    for job in HEARTBEAT_EXPECTATIONS.keys():
        raw = await redis.get(_key(job))
        if raw is None:
            out.append(
                Heartbeat(
                    job=job, last_run_ts=None, last_ok_ts=None, ok=False, error=None
                )
            )
            continue
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
        except Exception:  # noqa: BLE001
            out.append(
                Heartbeat(
                    job=job, last_run_ts=None, last_ok_ts=None, ok=False,
                    error="parse_error",
                )
            )
            continue
        out.append(
            Heartbeat(
                job=job,
                last_run_ts=data.get("last_run_ts"),
                last_ok_ts=data.get("last_ok_ts"),
                ok=bool(data.get("ok", False)),
                error=data.get("error"),
            )
        )
    return out


def classify(hb: Heartbeat, now: float | None = None) -> str:
    """Bucket a heartbeat into ok / stale / never."""
    if hb.last_ok_ts is None:
        return "never"
    n = now if now is not None else time.time()
    threshold = HEARTBEAT_EXPECTATIONS.get(hb.job, 24 * 3600)
    if (n - hb.last_ok_ts) > threshold:
        return "stale"
    return "ok"


def with_heartbeat(
    redis: Redis, job: str, fn: Callable[..., Awaitable[T]]
) -> Callable[..., Awaitable[T]]:
    """Wrap a coroutine so each call records a heartbeat after it finishes.

    Used at scheduler.add_job() time:
        scheduler.add_job(with_heartbeat(redis, "alert_runner", tick_alert_runner),
                          "interval", seconds=60, ...)

    On exception we still record (ok=False) before re-raising — keeps the
    failure visible in /health rather than only in logs.
    """

    async def _wrapped(*args: Any, **kwargs: Any) -> T:
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            try:
                await record(redis, job, ok=False, error=str(exc))
            except Exception as hb_exc:  # noqa: BLE001
                log.warning(
                    "heartbeat.record_failed",
                    job=job,
                    error=str(hb_exc),
                )
            raise
        try:
            await record(redis, job, ok=True)
        except Exception as hb_exc:  # noqa: BLE001
            log.warning("heartbeat.record_failed", job=job, error=str(hb_exc))
        return result

    return _wrapped
