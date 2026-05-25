"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  type ISeriesApi,
  type Time,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

interface PriceBar {
  time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
}

interface PriceSeriesResponse {
  instrument: string;
  interval: string;
  bars: PriceBar[];
}

interface Tick {
  exchange: string;
  symbol: string;
  ts: string;
  close: string;
  volume_cum: number;
}

interface CandleBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

type LiveStatus = "loading" | "live" | "reconnecting" | "offline" | "error";

const EXCHANGE = "KR";
const SYMBOL = "005930";

const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

function toDateStr(date: Date): string {
  // "YYYY-MM-DD" in UTC, matches our bar.time format
  return date.toISOString().slice(0, 10);
}

export default function Home() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const todayBarRef = useRef<CandleBar | null>(null);

  const [historyStatus, setHistoryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [historyError, setHistoryError] = useState<string>("");
  const [liveStatus, setLiveStatus] = useState<LiveStatus>("loading");
  const [lastTick, setLastTick] = useState<Tick | null>(null);
  const [barCount, setBarCount] = useState<number>(0);

  // Chart setup + history fetch
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
      timeScale: { timeVisible: false, secondsVisible: false },
    });

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

    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    fetch(`${apiUrl}/prices/${EXCHANGE}/${SYMBOL}?days=365`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<PriceSeriesResponse>;
      })
      .then((data) => {
        const candleData = data.bars.map((b) => ({
          time: b.time.slice(0, 10) as Time,
          open: Number(b.open),
          high: Number(b.high),
          low: Number(b.low),
          close: Number(b.close),
        }));
        const volumeData = data.bars.map((b) => ({
          time: b.time.slice(0, 10) as Time,
          value: b.volume,
          color: Number(b.close) >= Number(b.open) ? "#ef444466" : "#3b82f666",
        }));
        candleSeries.setData(candleData);
        volumeSeries.setData(volumeData);
        chart.timeScale().fitContent();

        // Cache today's bar (if present) so WebSocket ticks can mutate it
        const last = candleData[candleData.length - 1];
        if (last) {
          todayBarRef.current = {
            time: last.time as string,
            open: last.open,
            high: last.high,
            low: last.low,
            close: last.close,
          };
        }
        setBarCount(data.bars.length);
        setHistoryStatus("ready");
      })
      .catch((err: unknown) => {
        setHistoryError(err instanceof Error ? err.message : String(err));
        setHistoryStatus("error");
      });

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []);

  // WebSocket connection with auto-reconnect
  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const wsUrl = apiUrl.replace(/^http/, "ws") + `/ws/prices/${EXCHANGE}/${SYMBOL}`;

    let ws: WebSocket | null = null;
    let retryAttempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const applyTick = (tick: Tick) => {
      setLastTick(tick);
      setLiveStatus("live");

      const tickClose = Number(tick.close);
      const tickDate = new Date(tick.ts);
      const today = toDateStr(tickDate);

      const candleSeries = candleSeriesRef.current;
      const volumeSeries = volumeSeriesRef.current;
      if (!candleSeries || !volumeSeries) return;

      let bar = todayBarRef.current;
      if (!bar || bar.time !== today) {
        // First tick of a new day (or no prior data) — start fresh
        bar = {
          time: today,
          open: tickClose,
          high: tickClose,
          low: tickClose,
          close: tickClose,
        };
      } else {
        bar.high = Math.max(bar.high, tickClose);
        bar.low = Math.min(bar.low, tickClose);
        bar.close = tickClose;
      }
      todayBarRef.current = bar;

      candleSeries.update({
        time: bar.time as Time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      });
      volumeSeries.update({
        time: bar.time as Time,
        value: tick.volume_cum,
        color: bar.close >= bar.open ? "#ef444466" : "#3b82f666",
      });
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        retryAttempt = 0;
        setLiveStatus("live");
      };

      ws.onmessage = (event) => {
        try {
          const tick = JSON.parse(event.data) as Tick;
          applyTick(tick);
        } catch (err) {
          console.error("Invalid tick payload", err);
        }
      };

      ws.onerror = () => {
        // onclose will fire next — handle reconnect there
        setLiveStatus("reconnecting");
      };

      ws.onclose = (event) => {
        if (cancelled) return;
        // 4404 = our app-level "Instrument not found" — don't retry
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
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close();
      }
    };
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900">
              삼성전자 <span className="text-gray-400">KR:005930</span>
            </h1>
            <p className="mt-1 text-sm text-gray-600">최근 1년 일봉 (KOSPI) · 실시간 연동</p>
          </div>
          <LiveBadge status={liveStatus} lastTick={lastTick} />
        </header>

        {historyStatus === "error" && (
          <div className="mb-4 rounded-md bg-red-50 p-4 text-sm text-red-800">
            히스토리를 불러올 수 없습니다: {historyError}
            <br />
            <span className="text-red-600">
              백엔드가 {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}에서 실행 중인지 확인하세요.
            </span>
          </div>
        )}

        <div className="rounded-lg bg-white p-4 shadow">
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
              value={
                lastTick ? lastTick.volume_cum.toLocaleString() : barCount > 0 ? "—" : ""
              }
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
    loading: {
      dot: "bg-gray-300",
      label: "연결 중",
      tone: "bg-gray-50 text-gray-700",
    },
    live: {
      dot: "bg-green-500 animate-pulse",
      label: "LIVE",
      tone: "bg-green-50 text-green-800",
    },
    reconnecting: {
      dot: "bg-yellow-400 animate-pulse",
      label: "재연결 중",
      tone: "bg-yellow-50 text-yellow-800",
    },
    offline: {
      dot: "bg-gray-400",
      label: "OFFLINE",
      tone: "bg-gray-50 text-gray-700",
    },
    error: {
      dot: "bg-red-500",
      label: "ERROR",
      tone: "bg-red-50 text-red-800",
    },
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
