"""Build a compact context block for an instrument to feed into the LLM.

CLAUDE.md priority (high → low):
  1. 현재가, 등락률, 거래량
  2. 당일/최근 공시 헤드라인
  3. 최근 24h 뉴스 헤드라인
  4. 외국인/기관/개인 수급 요약 (커뮤니티 감성보다 KR 시장에선 더 강한 시그널)
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

from app.models import Disclosure, Instrument, InvestorFlow, NewsItem, Price

# Disclosure window for the LLM context. Last N days, capped to MAX entries.
# Samsung-class large caps file ~10/day (mostly operator share transactions);
# the cap keeps the prompt bounded while a date floor keeps stale items out.
DISCLOSURE_WINDOW_DAYS = 14
DISCLOSURE_MAX_COUNT = 20

# News window. Last 24h covers "what's moving the price right now";
# anything older is rarely the proximate cause of today's behavior.
NEWS_WINDOW_HOURS = 24
NEWS_MAX_COUNT = 15

# Investor flow window. 5 trading days captures the recent supply/demand
# story without dragging stale weeks into the prompt. We summarize as a
# small bullet list (cumulative + per-day count) — sending raw daily
# rows would burn ~10x more tokens for marginal LLM benefit.
INVESTOR_FLOW_WINDOW_DAYS = 5

log = structlog.get_logger()


@dataclass
class DisclosureSummary:
    """Minimal headline. Body is intentionally never loaded (CLAUDE.md
    policy + token budget)."""

    filed_at: datetime  # UTC
    title: str
    submitter: str | None


@dataclass
class NewsHeadline:
    """One news headline for the LLM. publisher provides minimal source
    attribution without exposing full article text."""

    published_at: datetime  # UTC
    title: str
    publisher: str | None


@dataclass
class VolumeSummary:
    """Daily-volume statistics derived from the 1y daily-bar series.

    The LLM previously had only `current_volume_cum` (today's running
    intraday total) — useless without a baseline. This adds the
    averages + spike detection + recent timeline so questions like
    "거래량 평소보다 많아?" / "최근 거래량 추이 어때?" can be answered
    from context without a tool call.

    All values are share counts (주); KR market data has no per-trade
    KRW notional in our pipeline.
    """

    avg_1y: int                     # 1년 평균 일거래량
    avg_20d: int                    # 20일 평균
    avg_5d: int                     # 5일 평균
    today_volume: int | None        # 오늘 거래량 (장중이면 누적 cum)
    today_vs_20d_ratio: float | None  # today / avg_20d (1.0 = 평균, 2.0 = 2배 spike)
    year_max_volume: int            # 1년 내 최대 거래량
    year_max_date: str              # 최대 거래량일 (YYYY-MM-DD)
    recent_7_days: list[tuple[str, int]]  # [(date, volume), ...] 최근 7거래일 (오래된→최근)


@dataclass
class InvestorFlowSummary:
    """Aggregated foreign/institutional/individual net buy over a window.
    Both cumulative (sum) and direction count (how many days net-buy vs
    net-sell) — the count is what reveals "외국인이 5일 연속 매도" stories."""

    days: int
    foreign_net_total: int
    foreign_buy_days: int
    foreign_sell_days: int
    institutional_net_total: int
    institutional_buy_days: int
    institutional_sell_days: int
    individual_net_total: int
    individual_buy_days: int
    individual_sell_days: int
    foreign_hold_ratio_latest: Decimal | None


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
    recent_news: list[NewsHeadline] = field(default_factory=list)
    investor_flow: InvestorFlowSummary | None = None
    volume_summary: VolumeSummary | None = None

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

        # 거래량 요약 — 1년 평균/20일/5일 + spike ratio + 최근 7일 시계열.
        # 가격 통계와 분리해 따로 섹션을 두면 LLM이 "거래량 측면" 질문에
        # 그 블록만 집어서 답하기 수월.
        if self.volume_summary is not None:
            vs = self.volume_summary
            lines.append("### 거래량 (일봉 기준)")
            lines.append(
                f"- 평균: 1년 {_fmt_share_count(vs.avg_1y)} · "
                f"20일 {_fmt_share_count(vs.avg_20d)} · "
                f"5일 {_fmt_share_count(vs.avg_5d)}"
            )
            if vs.today_volume is not None and vs.today_vs_20d_ratio is not None:
                spike_note = ""
                if vs.today_vs_20d_ratio >= 2.0:
                    spike_note = " — spike"
                elif vs.today_vs_20d_ratio <= 0.5:
                    spike_note = " — 부진"
                lines.append(
                    f"- 오늘: {_fmt_share_count(vs.today_volume)} "
                    f"(20일 평균의 {vs.today_vs_20d_ratio:.2f}배{spike_note})"
                )
            lines.append(
                f"- 1년 최대: {_fmt_share_count(vs.year_max_volume)} "
                f"({vs.year_max_date})"
            )
            if vs.recent_7_days:
                trail = " → ".join(
                    f"{d[5:]}: {_fmt_share_count(v)}"
                    for d, v in vs.recent_7_days
                )
                lines.append(f"- 최근 7일: {trail}")
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

        # 3. 최근 뉴스 헤드라인 — 본문 X (저작권/약관 + 토큰 예산)
        lines.append(
            f"### 최근 뉴스 (최근 {NEWS_WINDOW_HOURS}시간, 최대 {NEWS_MAX_COUNT}건)"
        )
        if self.recent_news:
            for n in self.recent_news:
                time_part = n.published_at.strftime("%m-%d %H:%M")
                publisher = f" · {n.publisher}" if n.publisher else ""
                lines.append(f"- {time_part}{publisher}: {n.title}")
        else:
            lines.append("- (해당 기간 뉴스 없음)")
        lines.append("")

        # 4. 최근 수급 (외국인/기관/개인 5거래일 누적 + 매수/매도 일수)
        lines.append(f"### 최근 수급 (최근 {INVESTOR_FLOW_WINDOW_DAYS}거래일)")
        flow = self.investor_flow
        if flow is None or flow.days == 0:
            lines.append("- (해당 기간 수급 데이터 없음)")
        else:
            lines.append(
                f"- 외국인: 순매수 {_fmt_volume(flow.foreign_net_total)}주 "
                f"(매수 {flow.foreign_buy_days}일 / 매도 {flow.foreign_sell_days}일)"
            )
            lines.append(
                f"- 기관: 순매수 {_fmt_volume(flow.institutional_net_total)}주 "
                f"(매수 {flow.institutional_buy_days}일 / 매도 {flow.institutional_sell_days}일)"
            )
            lines.append(
                f"- 개인: 순매수 {_fmt_volume(flow.individual_net_total)}주 "
                f"(매수 {flow.individual_buy_days}일 / 매도 {flow.individual_sell_days}일)"
            )
            if flow.foreign_hold_ratio_latest is not None:
                lines.append(
                    f"- 외국인 보유율(최신): {flow.foreign_hold_ratio_latest}%"
                )
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


def _fmt_volume(n: int) -> str:
    """Compact signed share counts: ±M for 백만 단위, ±k for 천 단위, else raw.
    Adds a leading + for positives so the LLM sees direction at a glance."""
    if n == 0:
        return "0"
    sign = "+" if n > 0 else "-"
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{sign}{abs_n / 1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"{sign}{abs_n / 1_000:.0f}K"
    return f"{sign}{abs_n}"


def _fmt_share_count(n: int) -> str:
    """Unsigned compact share count for absolute volume figures.
    Distinct from `_fmt_volume` (signed net flows) so the LLM doesn't
    see a "+" mark in front of plain daily-volume numbers and treat
    it as direction."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M주"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K주"
    return f"{n:,}주"


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
    # venue="KRX" filter is required after the Top10 NXT split — otherwise
    # the same trade-date appears twice (KRX + NXT) and indicators/year-
    # high/low compute on a duplicated series.
    bars = (
        (
            await db.execute(
                select(Price)
                .where(
                    Price.instrument_id == instrument.id,
                    Price.interval == "1d",
                    Price.venue == "KRX",
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

    # Recent news (priority 3). 24h window — anything older rarely explains
    # today's price action, and the daily prompt cost goes up otherwise.
    news_cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_WINDOW_HOURS)
    news_rows = (
        (
            await db.execute(
                select(NewsItem)
                .where(
                    NewsItem.instrument_id == instrument.id,
                    NewsItem.published_at >= news_cutoff,
                )
                .order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
                .limit(NEWS_MAX_COUNT)
            )
        )
        .scalars()
        .all()
    )
    recent_news = [
        NewsHeadline(
            published_at=n.published_at,
            title=n.title,
            publisher=n.publisher,
        )
        for n in news_rows
    ]

    # Investor flow summary (priority 4) — aggregate last N trading days.
    flow_rows = (
        (
            await db.execute(
                select(InvestorFlow)
                .where(InvestorFlow.instrument_id == instrument.id)
                .order_by(InvestorFlow.trade_date.desc())
                .limit(INVESTOR_FLOW_WINDOW_DAYS)
            )
        )
        .scalars()
        .all()
    )
    investor_flow: InvestorFlowSummary | None = None
    if flow_rows:
        f_total = sum(r.foreign_net_volume for r in flow_rows)
        i_total = sum(r.institutional_net_volume for r in flow_rows)
        p_total = sum(r.individual_net_volume for r in flow_rows)
        investor_flow = InvestorFlowSummary(
            days=len(flow_rows),
            foreign_net_total=int(f_total),
            foreign_buy_days=sum(1 for r in flow_rows if r.foreign_net_volume > 0),
            foreign_sell_days=sum(1 for r in flow_rows if r.foreign_net_volume < 0),
            institutional_net_total=int(i_total),
            institutional_buy_days=sum(
                1 for r in flow_rows if r.institutional_net_volume > 0
            ),
            institutional_sell_days=sum(
                1 for r in flow_rows if r.institutional_net_volume < 0
            ),
            individual_net_total=int(p_total),
            individual_buy_days=sum(
                1 for r in flow_rows if r.individual_net_volume > 0
            ),
            individual_sell_days=sum(
                1 for r in flow_rows if r.individual_net_volume < 0
            ),
            # flow_rows[0] is newest because of ORDER BY trade_date DESC
            foreign_hold_ratio_latest=flow_rows[0].foreign_hold_ratio,
        )

    # Volume summary (priority 1 — sits next to current price in CLAUDE.md
    # priority list). Computed from the same `bars` we already loaded so
    # there's no extra DB round-trip. Skipped when we have fewer than
    # ~20 bars (avg_20d would be meaningless).
    volume_summary: VolumeSummary | None = None
    if len(bars) >= 5:
        volumes = [int(b.volume) for b in bars]
        avg_1y = sum(volumes) // len(volumes)
        avg_20d = sum(volumes[-20:]) // min(20, len(volumes))
        avg_5d = sum(volumes[-5:]) // min(5, len(volumes))
        max_idx = max(range(len(volumes)), key=lambda i: volumes[i])
        year_max_volume = volumes[max_idx]
        year_max_date = bars[max_idx].time.strftime("%Y-%m-%d")
        recent_7 = [
            (b.time.strftime("%Y-%m-%d"), int(b.volume))
            for b in bars[-7:]
        ]

        # Today's volume: prefer realtime cum (intraday) over last EOD bar
        # so the spike ratio reflects the current trading day.
        today_vol = current_volume_cum if current_volume_cum is not None else (
            volumes[-1] if volumes else None
        )
        today_ratio = (
            (today_vol / avg_20d) if (today_vol is not None and avg_20d > 0) else None
        )
        volume_summary = VolumeSummary(
            avg_1y=avg_1y,
            avg_20d=avg_20d,
            avg_5d=avg_5d,
            today_volume=today_vol,
            today_vs_20d_ratio=today_ratio,
            year_max_volume=year_max_volume,
            year_max_date=year_max_date,
            recent_7_days=recent_7,
        )

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
        recent_news=recent_news,
        investor_flow=investor_flow,
        volume_summary=volume_summary,
    )


def _pct_change(closes: list[Decimal], window: int) -> float | None:
    if len(closes) <= window:
        return None
    past = closes[-window - 1]
    now = closes[-1]
    if past == 0:
        return None
    return float((now - past) / past * 100)
