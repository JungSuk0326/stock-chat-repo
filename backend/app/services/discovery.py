"""Screener evaluation engine + candidate lifecycle.

Pipeline per screener:
  1. Resolve universe from `instruments` (universe JSONB filter)
  2. Bulk-fetch last ~400d of daily bars for universe → compute per-symbol
     technical metrics (RSI/SMA/52w range/volume avg) in Python
  3. Apply `technical:*` criteria → narrows universe
  4. Cache-first fundamentals fetch for survivors (yfinance only on misses)
  5. Apply `fundamental:*` criteria → final candidates
  6. UPSERT into `candidates` honoring `dismissed`/active-`snoozed` rows

Condition types (MVP set):

  technical:
    rsi_below / rsi_above                          value: number (RSI 0-100)
    price_near_52w_low_within_pct                  value: number  (close <= low*(1+v%))
    price_near_52w_high_within_pct                 value: number  (close >= high*(1-v%))
    change_pct_5d_above / change_pct_5d_below      value: number
    change_pct_20d_above / change_pct_20d_below    value: number
    volume_spike_ratio_above                       value: number  (today/20d-avg)
    price_above_sma20 / price_below_sma20          value: ignored (bool)

  fundamental:
    per_below / per_above                          value: number (uses per || forward_per)
    pbr_below / pbr_above                          value: number
    market_cap_above / market_cap_below            value: integer (instrument currency)
    dividend_yield_above                           value: number (percent)
    sector_in                                      value: list[str]

NULL metric → criterion automatically NOT matched (conservative). The
runner never raises on bad data; symbols that lack a metric just don't
match that criterion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models import (
    Candidate,
    FundamentalsSnapshot,
    Instrument,
    Price,
    Screener,
)
from app.services.fundamentals.base import FundamentalsAdapter
from app.services.fundamentals_sync import get_fundamentals_for_instruments

log = structlog.get_logger()

# Need at least this many daily bars to compute meaningful RSI/SMA20.
MIN_BARS_FOR_EVAL = 25
# 252 trading days ≈ 1 year of bars — covers 52w range + 20d MA + RSI(14).
BAR_LOOKBACK_DAYS = 400
# Hard cap on instruments we'll fundamentals-fetch in one screener run.
# Tech criteria narrow first; if a screener is pure-fundamental on a
# wide universe, that means N yfinance calls — keep it bounded so a
# misconfigured screener can't blast Yahoo with thousands of requests.
MAX_FUNDAMENTAL_FETCH_PER_RUN = 200


# ----- Technical metrics -----


@dataclass
class TechnicalMetrics:
    close: float
    sma20: float | None
    sma60: float | None
    rsi14: float | None
    year_high: float
    year_low: float
    pct_change_5d: float | None
    pct_change_20d: float | None
    volume_today: int
    volume_avg_20d: float | None


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder-smoothed RSI. Mirrors backend/app/services/llm_context.py."""
    if len(closes) <= period:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _pct_change(closes: list[float], window: int) -> float | None:
    if len(closes) <= window:
        return None
    past = closes[-window - 1]
    if past == 0:
        return None
    return (closes[-1] - past) / past * 100


def _compute_metrics(closes: list[float], volumes: list[int]) -> TechnicalMetrics | None:
    if len(closes) < MIN_BARS_FOR_EVAL:
        return None
    return TechnicalMetrics(
        close=closes[-1],
        sma20=_sma(closes, 20),
        sma60=_sma(closes, 60),
        rsi14=_rsi(closes, 14),
        year_high=max(closes),
        year_low=min(closes),
        pct_change_5d=_pct_change(closes, 5),
        pct_change_20d=_pct_change(closes, 20),
        volume_today=volumes[-1] if volumes else 0,
        volume_avg_20d=(sum(volumes[-20:]) / 20) if len(volumes) >= 20 else None,
    )


async def _fetch_metrics_bulk(
    db: AsyncSession, instrument_ids: list[int]
) -> dict[int, TechnicalMetrics]:
    """One SELECT for all symbols' bars, then per-symbol metric computation
    in Python. With KOSPI-sized universes (~800) × ~250 bars = 200k rows,
    this fits comfortably in memory and takes <2s end-to-end."""
    if not instrument_ids:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=BAR_LOOKBACK_DAYS)
    stmt = (
        select(Price.instrument_id, Price.time, Price.close, Price.volume)
        .where(Price.instrument_id.in_(instrument_ids))
        .where(Price.interval == "1d")
        .where(Price.time >= cutoff)
        .order_by(Price.instrument_id, Price.time)
    )
    rows = (await db.execute(stmt)).all()

    grouped: dict[int, tuple[list[float], list[int]]] = {}
    for inst_id, _t, close, vol in rows:
        closes, volumes = grouped.setdefault(inst_id, ([], []))
        closes.append(float(close))
        volumes.append(int(vol or 0))

    out: dict[int, TechnicalMetrics] = {}
    for inst_id, (closes, volumes) in grouped.items():
        m = _compute_metrics(closes, volumes)
        if m is not None:
            out[inst_id] = m
    return out


# ----- Criterion evaluators -----


def _eval_technical(m: TechnicalMetrics, criterion: dict[str, Any]) -> tuple[bool, str | None]:
    """Return (matched, reason_fragment). reason_fragment is a short Korean
    string included in the candidate's `reason` for matching items."""
    ct = criterion.get("type", "")
    val = criterion.get("value")

    if ct == "technical:rsi_below":
        if m.rsi14 is None: return False, None
        ok = m.rsi14 < float(val)
        return ok, f"RSI {m.rsi14:.1f}" if ok else None
    if ct == "technical:rsi_above":
        if m.rsi14 is None: return False, None
        ok = m.rsi14 > float(val)
        return ok, f"RSI {m.rsi14:.1f}" if ok else None
    if ct == "technical:price_near_52w_low_within_pct":
        gap_pct = (m.close - m.year_low) / m.year_low * 100 if m.year_low else None
        if gap_pct is None: return False, None
        ok = gap_pct <= float(val)
        return ok, f"52w저점 {gap_pct:.1f}% 이내" if ok else None
    if ct == "technical:price_near_52w_high_within_pct":
        gap_pct = (m.year_high - m.close) / m.year_high * 100 if m.year_high else None
        if gap_pct is None: return False, None
        ok = gap_pct <= float(val)
        return ok, f"52w고점 {gap_pct:.1f}% 이내" if ok else None
    if ct == "technical:change_pct_5d_above":
        if m.pct_change_5d is None: return False, None
        ok = m.pct_change_5d > float(val)
        return ok, f"5d {m.pct_change_5d:+.2f}%" if ok else None
    if ct == "technical:change_pct_5d_below":
        if m.pct_change_5d is None: return False, None
        ok = m.pct_change_5d < float(val)
        return ok, f"5d {m.pct_change_5d:+.2f}%" if ok else None
    if ct == "technical:change_pct_20d_above":
        if m.pct_change_20d is None: return False, None
        ok = m.pct_change_20d > float(val)
        return ok, f"20d {m.pct_change_20d:+.2f}%" if ok else None
    if ct == "technical:change_pct_20d_below":
        if m.pct_change_20d is None: return False, None
        ok = m.pct_change_20d < float(val)
        return ok, f"20d {m.pct_change_20d:+.2f}%" if ok else None
    if ct == "technical:volume_spike_ratio_above":
        if m.volume_avg_20d is None or m.volume_avg_20d == 0: return False, None
        ratio = m.volume_today / m.volume_avg_20d
        ok = ratio > float(val)
        return ok, f"거래량 {ratio:.1f}배" if ok else None
    if ct == "technical:price_above_sma20":
        if m.sma20 is None: return False, None
        ok = m.close > m.sma20
        return ok, "SMA20 위" if ok else None
    if ct == "technical:price_below_sma20":
        if m.sma20 is None: return False, None
        ok = m.close < m.sma20
        return ok, "SMA20 아래" if ok else None

    log.warning("discovery.unknown_technical_criterion", type=ct)
    return False, None


def _eval_fundamental(
    snap: FundamentalsSnapshot | None, criterion: dict[str, Any]
) -> tuple[bool, str | None]:
    if snap is None:
        return False, None
    ct = criterion.get("type", "")
    val = criterion.get("value")

    def _per() -> Decimal | None:
        return snap.per if snap.per is not None else snap.forward_per

    if ct == "fundamental:per_below":
        p = _per()
        if p is None: return False, None
        ok = float(p) < float(val)
        return ok, f"PER {float(p):.1f}" if ok else None
    if ct == "fundamental:per_above":
        p = _per()
        if p is None: return False, None
        ok = float(p) > float(val)
        return ok, f"PER {float(p):.1f}" if ok else None
    if ct == "fundamental:pbr_below":
        if snap.pbr is None: return False, None
        ok = float(snap.pbr) < float(val)
        return ok, f"PBR {float(snap.pbr):.2f}" if ok else None
    if ct == "fundamental:pbr_above":
        if snap.pbr is None: return False, None
        ok = float(snap.pbr) > float(val)
        return ok, f"PBR {float(snap.pbr):.2f}" if ok else None
    if ct == "fundamental:market_cap_above":
        if snap.market_cap is None: return False, None
        ok = snap.market_cap > int(val)
        return ok, f"시총 {snap.market_cap / 1_000_000_000_000:.1f}조" if ok else None
    if ct == "fundamental:market_cap_below":
        if snap.market_cap is None: return False, None
        ok = snap.market_cap < int(val)
        return ok, f"시총 {snap.market_cap / 1_000_000_000_000:.1f}조" if ok else None
    if ct == "fundamental:dividend_yield_above":
        if snap.dividend_yield is None: return False, None
        ok = float(snap.dividend_yield) > float(val)
        return ok, f"배당 {float(snap.dividend_yield):.2f}%" if ok else None
    if ct == "fundamental:sector_in":
        if snap.sector is None: return False, None
        wanted = [str(s) for s in (val or [])]
        ok = snap.sector in wanted
        return ok, f"섹터 {snap.sector}" if ok else None

    log.warning("discovery.unknown_fundamental_criterion", type=ct)
    return False, None


# ----- Candidate upsert -----


async def _upsert_candidate(
    db: AsyncSession,
    *,
    user_id: int,
    instrument_id: int,
    source: str,
    reason: str,
    score: Decimal | None = None,
) -> bool:
    """Returns True if INSERTed or re-activated, False if skipped (because
    user has already dismissed this combination or actively snoozed it)."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Candidate)
        .where(Candidate.user_id == user_id)
        .where(Candidate.instrument_id == instrument_id)
        .where(Candidate.source == source)
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing is not None:
        if existing.status == "dismissed":
            return False
        if (
            existing.status == "snoozed"
            and existing.snoozed_until
            and existing.snoozed_until > now
        ):
            return False
        # Refresh reason / clear snooze / mark new for re-review
        existing.status = "new"
        existing.reason = reason
        existing.score = score
        existing.snoozed_until = None
        existing.discovered_at = now
        await db.commit()
        return True

    db.add(
        Candidate(
            user_id=user_id,
            instrument_id=instrument_id,
            source=source,
            status="new",
            reason=reason,
            score=score,
            discovered_at=now,
        )
    )
    await db.commit()
    return True


# ----- Screener runner -----


async def _resolve_universe(
    db: AsyncSession, universe: dict[str, Any]
) -> list[Instrument]:
    stmt = select(Instrument).where(Instrument.exchange == "KR")
    market = (universe or {}).get("market")
    if market:
        stmt = stmt.where(Instrument.market == market)
    return list((await db.execute(stmt)).scalars().all())


def _split_criteria(
    criteria: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tech = [c for c in criteria if str(c.get("type", "")).startswith("technical:")]
    fund = [c for c in criteria if str(c.get("type", "")).startswith("fundamental:")]
    return tech, fund


async def run_screener(
    screener_id: int, fundamentals_adapter: FundamentalsAdapter
) -> int:
    """Evaluate one screener and write candidates. Returns count of
    new+reactivated rows."""
    async with SessionLocal() as db:
        screener = (
            await db.execute(select(Screener).where(Screener.id == screener_id))
        ).scalar_one_or_none()
        if screener is None or not screener.enabled:
            return 0
        criteria = list(screener.criteria or [])
        tech_criteria, fund_criteria = _split_criteria(criteria)

        universe = await _resolve_universe(db, screener.universe or {})

        # Phase 1: technical filter (DB only, fast)
        metrics_map: dict[int, TechnicalMetrics] = {}
        if tech_criteria:
            metrics_map = await _fetch_metrics_bulk(db, [i.id for i in universe])
            survivors: list[tuple[Instrument, TechnicalMetrics, list[str]]] = []
            for inst in universe:
                m = metrics_map.get(inst.id)
                if m is None:
                    continue
                fragments: list[str] = []
                matched_all = True
                for c in tech_criteria:
                    ok, frag = _eval_technical(m, c)
                    if not ok:
                        matched_all = False
                        break
                    if frag:
                        fragments.append(frag)
                if matched_all:
                    survivors.append((inst, m, fragments))
        else:
            # No technical filter — pass everything through (need metrics
            # later only if reason building wants close prices)
            survivors = [(inst, None, []) for inst in universe]  # type: ignore[list-item]

        # Phase 2: fundamental filter on survivors
        if fund_criteria and survivors:
            instruments_to_fetch = [s[0] for s in survivors]
            if len(instruments_to_fetch) > MAX_FUNDAMENTAL_FETCH_PER_RUN:
                log.warning(
                    "discovery.universe_too_wide_for_fundamentals",
                    screener_id=screener.id,
                    survivors=len(instruments_to_fetch),
                    cap=MAX_FUNDAMENTAL_FETCH_PER_RUN,
                    note="add a technical criterion to narrow first",
                )
                # Truncate — protects Yahoo + bounds run time.
                instruments_to_fetch = instruments_to_fetch[:MAX_FUNDAMENTAL_FETCH_PER_RUN]
            fund_map = await get_fundamentals_for_instruments(
                fundamentals_adapter, instruments_to_fetch
            )
            final: list[tuple[Instrument, list[str]]] = []
            for inst, _m, frags in survivors:
                snap = fund_map.get(inst.id)
                matched_all = True
                new_frags = list(frags)
                for c in fund_criteria:
                    ok, frag = _eval_fundamental(snap, c)
                    if not ok:
                        matched_all = False
                        break
                    if frag:
                        new_frags.append(frag)
                if matched_all:
                    final.append((inst, new_frags))
        else:
            final = [(inst, frags) for inst, _m, frags in survivors]

        # Write candidates
        source = f"screener:{screener.id}"
        new_count = 0
        for inst, frags in final:
            reason = " · ".join(frags) if frags else screener.name
            ok = await _upsert_candidate(
                db,
                user_id=screener.user_id,
                instrument_id=inst.id,
                source=source,
                reason=reason,
            )
            if ok:
                new_count += 1

        # Update screener.last_run_at
        await db.execute(
            update(Screener)
            .where(Screener.id == screener.id)
            .values(last_run_at=datetime.now(timezone.utc))
        )
        await db.commit()

    log.info(
        "discovery.screener_done",
        screener_id=screener_id,
        universe_size=len(universe),
        matched=len(final),
        new_candidates=new_count,
    )
    return new_count


async def run_all_enabled_screeners(
    fundamentals_adapter: FundamentalsAdapter,
) -> dict[int, int]:
    """Daily cron entry point. Returns {screener_id: new_candidates}."""
    async with SessionLocal() as db:
        ids = list(
            (
                await db.execute(
                    select(Screener.id).where(Screener.enabled.is_(True))
                )
            ).scalars()
        )
    results: dict[int, int] = {}
    for sid in ids:
        try:
            results[sid] = await run_screener(sid, fundamentals_adapter)
        except Exception as exc:  # noqa: BLE001
            log.exception("discovery.screener_failed", screener_id=sid, error=str(exc))
            results[sid] = -1
    return results
