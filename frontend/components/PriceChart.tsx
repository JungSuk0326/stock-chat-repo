"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  type Time,
  createChart,
} from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getPrices, type Tick, wsPriceUrl } from "@/lib/api";
import {
  DEFAULT_SELECTED,
  INDICATORS,
  type IndicatorId,
  loadSelected,
  saveSelected,
} from "@/lib/chartIndicators";
import { bollinger, ema, rsi, sma } from "@/lib/indicators";

import { ChartSettings } from "./ChartSettings";

interface CandleBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

type LiveStatus = "loading" | "live" | "reconnecting" | "offline" | "error";

const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

function toDateStr(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * lightweight-charts time formats:
 *   - daily bars: `"YYYY-MM-DD"` (BusinessDay-shaped string)
 *   - intraday:   UTCTimestamp = unix seconds (integer)
 *
 * NXT solo chart shows 1m intraday so the x-axis must be timestamp-based.
 * Daily charts keep the date-string format which auto-positions one bar
 * per business day with KRX's familiar look.
 */
function toBarTime(isoOrDate: string, interval: string): Time {
  if (interval === "1m") {
    return Math.floor(new Date(isoOrDate).getTime() / 1000) as Time;
  }
  return isoOrDate.slice(0, 10) as Time;
}

/** Floor a date to its UTC minute (seconds since epoch). */
function utcMinuteSeconds(d: Date): number {
  return Math.floor(d.getTime() / 60000) * 60;
}

// ---------- Indicator-series ref shape ----------
// Bollinger is one toggle but three series; everything else is single.

type SingleSeriesRef = { kind: "single"; series: ISeriesApi<"Line"> };
type BollingerSeriesRef = {
  kind: "bollinger";
  upper: ISeriesApi<"Line">;
  middle: ISeriesApi<"Line">;
  lower: ISeriesApi<"Line">;
};
type IndicatorSeriesRef = SingleSeriesRef | BollingerSeriesRef;

function removeIndicatorSeries(chart: IChartApi, ref: IndicatorSeriesRef) {
  if (ref.kind === "single") {
    chart.removeSeries(ref.series);
  } else {
    chart.removeSeries(ref.upper);
    chart.removeSeries(ref.middle);
    chart.removeSeries(ref.lower);
  }
}

function sameSet<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

export type ChartVenue = "KRX" | "NXT" | "COMBINED";

interface Props {
  exchange: string;
  symbol: string;
  /**
   * Trading venue tab. KRX = 정규장 (default, pre-NXT behavior).
   * NXT = 넥스트레이드 ATS (08:00-20:00 KST).
   * COMBINED = KRX candles + NXT close as a dashed overlay line on top —
   *   indicators stay anchored to KRX since that's the "primary" reference.
   */
  venue?: ChartVenue;
}

export function PriceChart({ exchange, symbol, venue = "KRX" }: Props) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  // NXT overlay line in COMBINED mode. Holds NXT close as a single colored
  // line — no candles since the "primary" pane is KRX. todayBarNxtRef
  // tracks the current trading-day's last NXT close so live ticks can
  // update.replace the same point instead of pushing a new one each tick.
  const nxtLineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const todayBarRef = useRef<CandleBar | null>(null);
  const todayNxtTimeRef = useRef<string | null>(null);

  // KRX is the "primary" venue for COMBINED — it owns candles, volume, and
  // indicator inputs. NXT in COMBINED is a reference overlay only.
  const primaryVenue: "KRX" | "NXT" = venue === "NXT" ? "NXT" : "KRX";
  const showNxtOverlay = venue === "COMBINED";

  // NXT solo uses 1m intraday since the daily series only just started
  // accumulating (see app/services/nxt_eod_aggregator.py). 7-day window
  // keeps the bar count manageable (~5k candles max) while showing the
  // intraday flow that makes NXT worth watching. KRX/COMBINED stay on 1d.
  const primaryInterval: "1d" | "1m" = venue === "NXT" ? "1m" : "1d";
  const primaryDays = venue === "NXT" ? 7 : 365;
  const isIntraday = primaryInterval === "1m";

  // Indicator state — selection lives globally (localStorage), series refs
  // are per chart instance (lifecycle tied to the chart).
  //
  // Initialize with SSR-safe DEFAULT and hydrate from localStorage in an
  // effect. useState lazy initializers that touch `window` lose to
  // Next.js SSR — the server renders with DEFAULT, hydration freezes
  // that state, and localStorage is never consulted. The effect below
  // pulls the real selection on first client render, triggering the
  // settings-sync effect to recreate the series.
  const [selected, setSelected] = useState<Set<IndicatorId>>(
    () => new Set(DEFAULT_SELECTED),
  );

  useEffect(() => {
    const stored = loadSelected();
    // Skip the state update if the persisted value is already the same —
    // avoids a needless second render + indicator series rebuild on the
    // common case where the user has the default selection.
    if (sameSet(stored, new Set(DEFAULT_SELECTED))) return;
    setSelected(stored);
  }, []);

  const indicatorSeriesRef = useRef<Map<IndicatorId, IndicatorSeriesRef>>(
    new Map(),
  );
  const closesRef = useRef<number[]>([]);
  const timesRef = useRef<string[]>([]);

  const [historyStatus, setHistoryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [historyError, setHistoryError] = useState<string>("");
  const [liveStatus, setLiveStatus] = useState<LiveStatus>("loading");
  const [lastTick, setLastTick] = useState<Tick | null>(null);
  const [barCount, setBarCount] = useState<number>(0);

  // Reset transient state whenever symbol changes
  useEffect(() => {
    setHistoryStatus("loading");
    setHistoryError("");
    setLiveStatus("loading");
    setLastTick(null);
    setBarCount(0);
    todayBarRef.current = null;
    closesRef.current = [];
    timesRef.current = [];
  }, [exchange, symbol]);

  // ----- Indicator computation + render -----
  // Compute one indicator's data + push into its series. Called on:
  //   (a) initial history load, (b) settings change for newly-added, (c) every tick.
  const applyIndicator = useCallback((id: IndicatorId) => {
    const chart = chartRef.current;
    const ref = indicatorSeriesRef.current.get(id);
    if (!chart || !ref) return;

    const closes = closesRef.current;
    const times = timesRef.current;
    if (closes.length === 0) return;

    const lineData = (
      values: (number | null)[],
    ): { time: Time; value: number }[] =>
      values
        .map((v, i) =>
          v === null ? null : { time: times[i] as Time, value: v },
        )
        .filter((p): p is { time: Time; value: number } => p !== null);

    if (id === "sma_5")   (ref as SingleSeriesRef).series.setData(lineData(sma(closes, 5)));
    else if (id === "sma_20")  (ref as SingleSeriesRef).series.setData(lineData(sma(closes, 20)));
    else if (id === "sma_60")  (ref as SingleSeriesRef).series.setData(lineData(sma(closes, 60)));
    else if (id === "sma_120") (ref as SingleSeriesRef).series.setData(lineData(sma(closes, 120)));
    else if (id === "ema_12")  (ref as SingleSeriesRef).series.setData(lineData(ema(closes, 12)));
    else if (id === "ema_26")  (ref as SingleSeriesRef).series.setData(lineData(ema(closes, 26)));
    else if (id === "rsi_14")  (ref as SingleSeriesRef).series.setData(lineData(rsi(closes, 14)));
    else if (id === "bb_20_2") {
      const bb = bollinger(closes, 20, 2);
      const r = ref as BollingerSeriesRef;
      r.upper.setData(lineData(bb.upper));
      r.middle.setData(lineData(bb.middle));
      r.lower.setData(lineData(bb.lower));
    }
  }, []);

  const applyAllIndicators = useCallback(() => {
    indicatorSeriesRef.current.forEach((_, id) => applyIndicator(id));
  }, [applyIndicator]);

  // Chart setup + history fetch (per-symbol)
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const container = chartContainerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 500,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#1f2937",
      },
      grid: {
        vertLines: { color: "#f3f4f6" },
        horzLines: { color: "#f3f4f6" },
      },
      timeScale: {
        timeVisible: isIntraday,  // NXT 1m needs HH:MM on the x-axis
        secondsVisible: false,
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#ef4444",
      downColor: "#3b82f6",
      borderVisible: false,
      wickUpColor: "#ef4444",
      wickDownColor: "#3b82f6",
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    // COMBINED mode: an extra dashed line series sits ON TOP of the candle
    // pane and tracks NXT close. Same price scale as candles so the two
    // venues' prices are directly comparable on the y-axis.
    if (showNxtOverlay) {
      const nxtSeries = chart.addSeries(LineSeries, {
        color: "#7c3aed",  // 보라색 — 지표 색과 안 겹침
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      nxtLineSeriesRef.current = nxtSeries;
    }

    let cancelled = false;

    // Primary series fetch (KRX or NXT depending on `venue` prop).
    getPrices({
      exchange,
      symbol,
      days: primaryDays,
      interval: primaryInterval,
      venue: primaryVenue,
    })
      .then((data) => {
        if (cancelled) return;
        const candleData = data.bars.map((b) => ({
          time: toBarTime(b.time, primaryInterval),
          open: Number(b.open),
          high: Number(b.high),
          low: Number(b.low),
          close: Number(b.close),
        }));
        const volumeData = data.bars.map((b) => ({
          time: toBarTime(b.time, primaryInterval),
          value: b.volume,
          color: Number(b.close) >= Number(b.open) ? "#ef444466" : "#3b82f666",
        }));
        candleSeries.setData(candleData);
        volumeSeries.setData(volumeData);
        chart.timeScale().fitContent();

        closesRef.current = candleData.map((b) => b.close);
        // For indicator alignment we need parallel `times`. Stored as raw
        // bar value (string for 1d, number for 1m) — only consumed by
        // applyIndicator which passes it back to lightweight-charts.
        timesRef.current = candleData.map((b) => b.time as unknown as string);

        const last = candleData[candleData.length - 1];
        if (last) {
          todayBarRef.current = {
            time: last.time as unknown as string,
            open: last.open,
            high: last.high,
            low: last.low,
            close: last.close,
          };
        }
        setBarCount(data.bars.length);
        setHistoryStatus("ready");
        // Indicator series are created + populated by the settings-sync
        // effect, which re-runs on this historyStatus transition.
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setHistoryError(err instanceof Error ? err.message : String(err));
        setHistoryStatus("error");
      });

    // NXT overlay fetch (COMBINED mode only). Independent promise — a slow
    // or empty NXT response shouldn't delay the primary chart render.
    if (showNxtOverlay) {
      getPrices({ exchange, symbol, days: 365, venue: "NXT" })
        .then((data) => {
          if (cancelled) return;
          const series = nxtLineSeriesRef.current;
          if (!series) return;
          const lineData = data.bars.map((b) => ({
            time: b.time.slice(0, 10) as Time,
            value: Number(b.close),
          }));
          series.setData(lineData);
          const lastNxt = data.bars[data.bars.length - 1];
          if (lastNxt) todayNxtTimeRef.current = lastNxt.time.slice(0, 10);
        })
        .catch((err: unknown) => {
          // Non-fatal — log only. The KRX side stays usable.
          console.warn("NXT overlay fetch failed:", err);
        });
    }

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      cancelled = true;
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      nxtLineSeriesRef.current = null;
      todayNxtTimeRef.current = null;
      // Indicator series live on the chart we just destroyed — drop the
      // refs so the settings-sync effect knows to recreate them when a
      // new chart mounts.
      indicatorSeriesRef.current = new Map();
    };
  }, [exchange, symbol, applyAllIndicators, primaryVenue, showNxtOverlay]);

  // ----- Sync indicator series with selection -----
  // Reconciles series with `selected` in one shot. Gated on closes
  // being populated so we never end up with a series whose pane is
  // allocated but whose setData was never called — that intermediate
  // state, with lightweight-charts v5 + paneIndex, sometimes leaves
  // the pane visible but the line invisible.
  //
  // historyStatus is a dep so the effect re-runs when data lands and
  // creates all series at once with setData already applied.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (closesRef.current.length === 0) return;

    const current = indicatorSeriesRef.current;

    // Remove deselected first — frees up the pane slot if RSI is the
    // only pane-1 occupant and the user just unchecked it.
    for (const id of Array.from(current.keys())) {
      if (!selected.has(id)) {
        removeIndicatorSeries(chart, current.get(id)!);
        current.delete(id);
      }
    }

    // Add missing
    for (const meta of INDICATORS) {
      if (!selected.has(meta.id) || current.has(meta.id)) continue;

      if (meta.id === "bb_20_2") {
        const opts = {
          color: meta.color,
          lineWidth: 1 as const,
          priceLineVisible: false,
          lastValueVisible: false,
        };
        const upper = chart.addSeries(LineSeries, {
          ...opts,
          lineStyle: LineStyle.Dashed,
        });
        const middle = chart.addSeries(LineSeries, opts);
        const lower = chart.addSeries(LineSeries, {
          ...opts,
          lineStyle: LineStyle.Dashed,
        });
        current.set(meta.id, { kind: "bollinger", upper, middle, lower });
      } else {
        // RSI gets its own pane (paneIndex=1); everything else overlays.
        // Overlay MAs are kept thin (1px) so the candlesticks remain the
        // visual focus. RSI stays at 2px because it owns its pane.
        const series = chart.addSeries(
          LineSeries,
          {
            color: meta.color,
            lineWidth: meta.pane === 0 ? 1 : 2,
            priceLineVisible: false,
            lastValueVisible: meta.pane === 0, // hide on RSI pane to reduce clutter
          },
          meta.pane,
        );
        current.set(meta.id, { kind: "single", series });

        // RSI: lock the pane price scale to 0-100 and draw 30/70 guides.
        if (meta.id === "rsi_14") {
          series.priceScale().applyOptions({
            autoScale: false,
            scaleMargins: { top: 0.05, bottom: 0.05 },
          });
          series.applyOptions({
            autoscaleInfoProvider: () => ({
              priceRange: { minValue: 0, maxValue: 100 },
            }),
          });
          series.createPriceLine({
            price: 70,
            color: "#ef4444",
            lineStyle: LineStyle.Dashed,
            lineWidth: 1,
            axisLabelVisible: true,
            title: "70",
          });
          series.createPriceLine({
            price: 30,
            color: "#3b82f6",
            lineStyle: LineStyle.Dashed,
            lineWidth: 1,
            axisLabelVisible: true,
            title: "30",
          });
        }
      }
      applyIndicator(meta.id);
    }
  }, [selected, applyIndicator, historyStatus]);

  const handleSelectedChange = useCallback((next: Set<IndicatorId>) => {
    setSelected(next);
    saveSelected(next);
  }, []);

  // WebSocket connection with auto-reconnect (per-symbol)
  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryAttempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const applyTick = (tick: Tick) => {
      // Each WS message carries `venue`. Route by mode:
      //   - KRX or NXT solo: only that venue's ticks update the candles.
      //   - COMBINED: KRX ticks update candles+volume+indicators (primary),
      //     NXT ticks update only the NXT overlay line.
      const tickVenue = tick.venue ?? "KRX";
      const isPrimary = tickVenue === primaryVenue;
      const isNxtOverlay = showNxtOverlay && tickVenue === "NXT";
      if (!isPrimary && !isNxtOverlay) return;

      // LiveBadge shows the last tick of either venue — feels more "alive".
      setLastTick(tick);
      setLiveStatus("live");

      const tickClose = Number(tick.close);
      const tickDate = new Date(tick.ts);
      // Bar identity: for 1d charts it's the calendar date; for 1m it's
      // the UTC-minute timestamp (in seconds). Either type is `Time` from
      // lightweight-charts' perspective.
      const barTime: Time = isIntraday
        ? (utcMinuteSeconds(tickDate) as Time)
        : (toDateStr(tickDate) as Time);

      if (isNxtOverlay) {
        // Just update the NXT line; don't touch candles/indicators.
        const nxtSeries = nxtLineSeriesRef.current;
        if (!nxtSeries) return;
        nxtSeries.update({ time: barTime, value: tickClose });
        todayNxtTimeRef.current = String(barTime);
        return;
      }

      const candleSeries = candleSeriesRef.current;
      const volumeSeries = volumeSeriesRef.current;
      if (!candleSeries || !volumeSeries) return;

      let bar = todayBarRef.current;
      const isNewBar = !bar || String(bar.time) !== String(barTime);
      if (isNewBar) {
        bar = {
          time: String(barTime),
          open: tickClose,
          high: tickClose,
          low: tickClose,
          close: tickClose,
        };
      } else {
        bar!.high = Math.max(bar!.high, tickClose);
        bar!.low = Math.min(bar!.low, tickClose);
        bar!.close = tickClose;
      }
      todayBarRef.current = bar;

      candleSeries.update({
        time: barTime,
        open: bar!.open,
        high: bar!.high,
        low: bar!.low,
        close: bar!.close,
      });
      volumeSeries.update({
        time: barTime,
        value: tick.volume_cum,
        color: bar!.close >= bar!.open ? "#ef444466" : "#3b82f666",
      });

      // Keep closes/times in sync with the live bar so indicators reflect
      // the current price, not the prior bar's close.
      const closes = closesRef.current;
      const times = timesRef.current;
      if (closes.length === 0) return; // history hasn't loaded yet

      if (isNewBar) {
        closes.push(bar!.close);
        times.push(bar!.time);
      } else {
        closes[closes.length - 1] = bar!.close;
      }
      // Recompute all enabled indicators. Series count is small (≤ ~9)
      // and bar count is ≤ ~250, so this is well under 1ms even on slow
      // devices.
      applyAllIndicators();
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(wsPriceUrl(exchange, symbol));

      ws.onopen = () => {
        retryAttempt = 0;
        setLiveStatus("live");
      };
      ws.onmessage = (event) => {
        try {
          applyTick(JSON.parse(event.data) as Tick);
        } catch (err) {
          console.error("Invalid tick payload", err);
        }
      };
      ws.onerror = () => setLiveStatus("reconnecting");
      ws.onclose = (event) => {
        if (cancelled) return;
        if (event.code === 4404) {
          setLiveStatus("error");
          return;
        }
        setLiveStatus("reconnecting");
        const delay =
          RECONNECT_BACKOFF_MS[Math.min(retryAttempt, RECONNECT_BACKOFF_MS.length - 1)];
        retryAttempt += 1;
        retryTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (ws && ws.readyState <= WebSocket.OPEN) ws.close();
    };
  }, [exchange, symbol, applyAllIndicators, primaryVenue, showNxtOverlay]);

  const enabledIndicatorsLegend = useMemo(
    () => INDICATORS.filter((i) => selected.has(i.id)),
    [selected],
  );

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-4">
        <LiveBadge status={liveStatus} lastTick={lastTick} />
        <div className="flex items-center gap-2">
          {historyStatus === "error" && (
            <span className="text-xs text-red-600">히스토리 오류: {historyError}</span>
          )}
          <ChartSettings selected={selected} onChange={handleSelectedChange} />
        </div>
      </div>

      <div className="rounded-lg bg-white p-4 shadow">
        {enabledIndicatorsLegend.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-gray-600">
            {enabledIndicatorsLegend.map((ind) => (
              <span key={ind.id} className="inline-flex items-center gap-1">
                <span
                  className="inline-block h-2 w-3 rounded-sm"
                  style={{ backgroundColor: ind.color }}
                />
                {ind.label}
              </span>
            ))}
          </div>
        )}
        <div ref={chartContainerRef} />
        {historyStatus === "loading" && (
          <p className="mt-2 text-center text-sm text-gray-500">불러오는 중...</p>
        )}
      </div>

      {(historyStatus === "ready" || lastTick) && (
        <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat
            label="현재가"
            value={
              lastTick
                ? `${Number(lastTick.close).toLocaleString()}원`
                : todayBarRef.current
                  ? `${todayBarRef.current.close.toLocaleString()}원`
                  : "—"
            }
            highlight
          />
          <Stat
            label="누적 거래량"
            value={lastTick ? lastTick.volume_cum.toLocaleString() : "—"}
          />
          <Stat label="거래일 수" value={`${barCount}`} />
          <Stat
            label="마지막 업데이트"
            value={
              lastTick
                ? new Date(lastTick.ts).toLocaleTimeString("ko-KR", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })
                : "—"
            }
          />
        </dl>
      )}
    </div>
  );
}

function LiveBadge({
  status,
  lastTick,
}: {
  status: LiveStatus;
  lastTick: Tick | null;
}) {
  const config: Record<LiveStatus, { dot: string; label: string; tone: string }> = {
    loading: { dot: "bg-gray-300", label: "연결 중", tone: "bg-gray-50 text-gray-700" },
    live: { dot: "bg-green-500 animate-pulse", label: "LIVE", tone: "bg-green-50 text-green-800" },
    reconnecting: { dot: "bg-yellow-400 animate-pulse", label: "재연결 중", tone: "bg-yellow-50 text-yellow-800" },
    offline: { dot: "bg-gray-400", label: "OFFLINE", tone: "bg-gray-50 text-gray-700" },
    error: { dot: "bg-red-500", label: "ERROR", tone: "bg-red-50 text-red-800" },
  };
  const c = config[status];
  return (
    <div className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${c.tone}`}>
      <span className={`h-2 w-2 rounded-full ${c.dot}`} />
      {c.label}
      {status === "live" && lastTick && (
        <span className="text-gray-500">
          · {new Date(lastTick.ts).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
        </span>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className={`rounded-lg bg-white p-3 shadow-sm ${highlight ? "ring-2 ring-blue-100" : ""}`}>
      <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className={`mt-1 text-lg font-semibold ${highlight ? "text-blue-700" : "text-gray-900"}`}>
        {value}
      </dd>
    </div>
  );
}
