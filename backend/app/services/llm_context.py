"""Build a compact context block for an instrument to feed into the LLM.

CLAUDE.md priority (high → low):
  1. 현재가, 등락률, 거래량
  2. 당일/최근 공시 헤드라인
  3. 최근 24h 뉴스 헤드라인           ← 미구현
  4. 커뮤니티 감성 집계               ← 미구현
  5. 기술적 지표 (이동평균, RSI 등)
  6. 과거 차트 요약 (1주/1개월 추세)

Target size: ~1500 tokens.

The output is Korean prose with numbers — that's what the LLM has to reason
about, and human-readable formatting helps both the LLM and debugging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Disclosure, Instrument, Price

# Disclosure window for the LLM context. Last N days, capped to MAX entries.
# Samsung-class large caps file ~10/day (mostly operator share transactions);
# the cap keeps the prompt bounded while a date floor keeps stale items out.
DISCLOSURE_WINDOW_DAYS = 14
DISCLOSURE_MAX_COUNT = 20

log = structlog.get_logger()


@dataclass
class DisclosureSummary:
    """Minimal headline. Body is intentionally never loaded (CLAUDE.md
    policy + token budget)."""

    filed_at: datetime  # UTC
    title: str
    submitter: str | None


@dataclass
class LLMContext:
    """Structured context bundle. The `as_text()` method formats for the LLM."""

    canonical_id: str
    name: str | None
    market: str | None
    currency: str

    current_price: Decimal | None
    current_volume_cum: int | None
    current_ts: datetime | None

    prev_close: Decimal | None  # yesterday's close (or last available)
    change_pct_day: float | None

    week_change_pct: float | None
    month_change_pct: float | None
    year_high: Decimal | None
    year_low: Decimal | None

    ma5: Decimal | None
    ma20: Decimal | None
    ma60: Decimal | None
    rsi14: float | None

    recent_bars_count: int

    recent_disclosures: list[DisclosureSummary] = field(default_factory=list)

    def as_text(self) -> str:
        ccy = self.currency
        sym_line = f"{self.name or '?'} ({self.canonical_id})"
        if self.market:
            sym_line += f" · {self.market}"

        lines = [f"## 종목: {sym_line}", ""]

        # 1. 현재가
        lines.append("### 현재 시세")
        if self.current_price is not None:
            lines.append(f"- 현재가: {_fmt_money(self.current_price, ccy)}")
            if self.change_pct_day is not None:
                arrow = "▲" if self.change_pct_day >= 0 else "▼"
                lines.append(
                    f"- 전일 대비: {arrow} {self.change_pct_day:+.2f}% "
                    f"(전일 종가 {_fmt_money(self.prev_close, ccy)})"
                )
            if self.current_volume_cum is not None:
                lines.append(f"- 누적 거래량: {self.current_volume_cum:,}주")
            if self.current_ts:
                lines.append(f"- 시점: {self.current_ts.isoformat()}")
        else:
            lines.append("- (실시간 가격 데이터 없음)")
        lines.append("")

        # 6. 과거 추세 요약
        lines.append("### 추세 요약")
        if self.week_change_pct is not None:
            lines.append(f"- 1주 변화: {self.week_change_pct:+.2f}%")
        if self.month_change_pct is not None:
            lines.append(f"- 1개월 변화: {self.month_change_pct:+.2f}%")
        if self.year_high is not None and self.year_low is not None:
            lines.append(
                f"- 1년 범위: 저 {_fmt_money(self.year_low, ccy)} ~ "
                f"고 {_fmt_money(self.year_high, ccy)}"
            )
        lines.append(f"- 보유 일봉 수: {self.recent_bars_count}")
        lines.append("")

        # 2. 최근 공시 — 헤드라인만, 본문 X (CLAUDE.md 정책 + 토큰 예산)
        lines.append(
            f"### 최근 공시 (최근 {DISCLOSURE_WINDOW_DAYS}일, 최대 {DISCLOSURE_MAX_COUNT}건)"
        )
        if self.recent_disclosures:
            for d in self.recent_disclosures:
                date_part = d.filed_at.strftime("%m-%d")
                submitter = f" · {d.submitter}" if d.submitter else ""
                lines.append(f"- {date_part}{submitter}: {d.title}")
        else:
            lines.append("- (해당 기간 공시 없음)")
        lines.append("")

        # 5. 기술적 지표
        lines.append("### 기술적 지표")
        if self.ma5 is not None:
            lines.append(f"- MA5:  {_fmt_money(self.ma5, ccy)}")
        if self.ma20 is not None:
            lines.append(f"- MA20: {_fmt_money(self.ma20, ccy)}")
        if self.ma60 is not None:
            lines.append(f"- MA60: {_fmt_money(self.ma60, ccy)}")
        if self.rsi14 is not None:
            lines.append(f"- RSI(14): {self.rsi14:.1f}")
        lines.append("")

        return "\n".join(lines).rstrip()


def _fmt_money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return "—"
    if currency == "KRW":
        return f"{int(amount):,}원"
    return f"{amount:.2f} {currency}"


def _sma(values: list[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    head = values[-window:]
    return sum(head, Decimal(0)) / Decimal(window)


def _rsi(values: list[Decimal], period: int = 14) -> float | None:
    """Wilder-smoothed RSI on a list of closes (chronological)."""
    if len(values) < period + 1:
        return None
    gains = Decimal(0)
    losses = Decimal(0)
    # Initial average from the first `period` diffs
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    # Wilder smoothing for the rest
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else Decimal(0)
        loss = -diff if diff < 0 else Decimal(0)
        avg_gain = (avg_gain * (period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + loss) / Decimal(period)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


async def assemble_context(
    db: AsyncSession,
    redis: Redis,
    exchange: str,
    symbol: str,
) -> LLMContext | None:
    """Build the LLM context for `{exchange}:{symbol}`.

    Returns None only if the instrument doesn't exist in DB. Otherwise returns
    a context with whatever data is available (some fields may be None).
    """
    instrument = (
        await db.execute(
            select(Instrument).where(
                Instrument.exchange == exchange,
                Instrument.symbol == symbol,
            )
        )
    ).scalar_one_or_none()
    if instrument is None:
        return None

    # Daily closes for trend + indicators (oldest first, recent last).
    bars = (
        (
            await db.execute(
                select(Price)
                .where(
                    Price.instrument_id == instrument.id,
                    Price.interval == "1d",
                )
                .order_by(Price.time.asc())
            )
        )
        .scalars()
        .all()
    )
    closes = [b.close for b in bars]

    # Realtime: Redis cache first, fall back to last 1d close.
    current_price: Decimal | None = None
    current_volume_cum: int | None = None
    current_ts: datetime | None = None
    cached_raw = await redis.get(f"price:{exchange}:{symbol}")
    if cached_raw:
        try:
            cached = json.loads(cached_raw if isinstance(cached_raw, str) else cached_raw.decode())
            current_price = Decimal(str(cached["close"]))
            current_volume_cum = int(cached["volume_cum"])
            current_ts = datetime.fromisoformat(cached["ts"])
        except Exception as exc:  # noqa: BLE001
            log.warning("llm_context.cache_parse_failed", error=str(exc))

    last_bar = bars[-1] if bars else None
    if current_price is None and last_bar:
        current_price = last_bar.close
        current_ts = last_bar.time
    prev_close = bars[-2].close if len(bars) >= 2 else None

    # Day change %: prefer cached current vs last EOD close
    change_pct_day: float | None = None
    if current_price is not None and prev_close is not None and prev_close != 0:
        change_pct_day = float((current_price - prev_close) / prev_close * 100)

    # Trend windows (5 trading days ≈ 1 week, 20 ≈ 1 month)
    week_change_pct = _pct_change(closes, 5)
    month_change_pct = _pct_change(closes, 20)
    year_high = max(closes) if closes else None
    year_low = min(closes) if closes else None

    # Recent disclosures (priority 2). Date floor + count cap together bound
    # the prompt for noisy filers like Samsung.
    cutoff = datetime.now(timezone.utc) - timedelta(days=DISCLOSURE_WINDOW_DAYS)
    disclosure_rows = (
        (
            await db.execute(
                select(Disclosure)
                .where(
                    Disclosure.instrument_id == instrument.id,
                    Disclosure.filed_at >= cutoff,
                )
                .order_by(Disclosure.filed_at.desc(), Disclosure.id.desc())
                .limit(DISCLOSURE_MAX_COUNT)
            )
        )
        .scalars()
        .all()
    )
    recent_disclosures = [
        DisclosureSummary(
            filed_at=d.filed_at,
            title=d.title,
            submitter=d.submitter,
        )
        for d in disclosure_rows
    ]

    return LLMContext(
        canonical_id=f"{instrument.exchange}:{instrument.symbol}",
        name=instrument.name,
        market=instrument.market,
        currency=instrument.currency,
        current_price=current_price,
        current_volume_cum=current_volume_cum,
        current_ts=current_ts.astimezone(timezone.utc) if current_ts else None,
        prev_close=prev_close,
        change_pct_day=change_pct_day,
        week_change_pct=week_change_pct,
        month_change_pct=month_change_pct,
        year_high=year_high,
        year_low=year_low,
        ma5=_sma(closes, 5),
        ma20=_sma(closes, 20),
        ma60=_sma(closes, 60),
        rsi14=_rsi(closes, 14),
        recent_bars_count=len(bars),
        recent_disclosures=recent_disclosures,
    )


def _pct_change(closes: list[Decimal], window: int) -> float | None:
    if len(closes) <= window:
        return None
    past = closes[-window - 1]
    now = closes[-1]
    if past == 0:
        return None
    return float((now - past) / past * 100)
