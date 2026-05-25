"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
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

export default function Home() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [barCount, setBarCount] = useState<number>(0);
  const [latest, setLatest] = useState<PriceBar | null>(null);

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
        timeVisible: false,
        secondsVisible: false,
      },
    });

    // KR 관례: 상승=빨강, 하락=파랑
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

    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

    fetch(`${apiUrl}/prices/KR/005930?days=365`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<PriceSeriesResponse>;
      })
      .then((data) => {
        const candleData = data.bars.map((b) => ({
          time: b.time.slice(0, 10), // "YYYY-MM-DD"
          open: Number(b.open),
          high: Number(b.high),
          low: Number(b.low),
          close: Number(b.close),
        }));
        const volumeData = data.bars.map((b) => ({
          time: b.time.slice(0, 10),
          value: b.volume,
          color:
            Number(b.close) >= Number(b.open) ? "#ef444466" : "#3b82f666",
        }));
        candleSeries.setData(candleData);
        volumeSeries.setData(volumeData);
        chart.timeScale().fitContent();

        setBarCount(data.bars.length);
        setLatest(data.bars[data.bars.length - 1] ?? null);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        setErrorMsg(msg);
        setStatus("error");
      });

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900">
            삼성전자 <span className="text-gray-400">KR:005930</span>
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            최근 1년 일봉 (KOSPI)
          </p>
        </header>

        {status === "error" && (
          <div className="mb-4 rounded-md bg-red-50 p-4 text-sm text-red-800">
            데이터를 불러올 수 없습니다: {errorMsg}
            <br />
            <span className="text-red-600">
              백엔드가 {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}에서 실행 중인지 확인하세요.
            </span>
          </div>
        )}

        <div className="rounded-lg bg-white p-4 shadow">
          <div ref={chartContainerRef} />
          {status === "loading" && (
            <p className="mt-2 text-center text-sm text-gray-500">불러오는 중...</p>
          )}
        </div>

        {status === "ready" && latest && (
          <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="거래일 수" value={`${barCount}`} />
            <Stat label="최근 종가" value={`${Number(latest.close).toLocaleString()}원`} />
            <Stat label="최근 거래량" value={latest.volume.toLocaleString()} />
            <Stat label="최근 거래일" value={latest.time.slice(0, 10)} />
          </dl>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white p-3 shadow-sm">
      <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="mt-1 text-lg font-semibold text-gray-900">{value}</dd>
    </div>
  );
}
