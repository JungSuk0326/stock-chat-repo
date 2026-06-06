"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { AlertsPanel } from "@/components/AlertsPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { MarketFeedPanel } from "@/components/MarketFeedPanel";
import { PriceChart, type ChartVenue } from "@/components/PriceChart";
import { WatchlistPanel } from "@/components/WatchlistPanel";

const DEFAULT_SELECTED = "KR:005930";

/**
 * NXT(넥스트레이드) UI 토글. 현재 false — 백엔드는 NXT 폴링 + 1m→1d 집계를
 * 계속하지만, UI 탭은 숨김. 이유:
 *   - KRX는 1d, NXT는 1m이라 시각적 통일성이 부족
 *   - NXT 일봉이 충분히 쌓이려면 시간 필요 (출범 1년+, 실시간 외 공개 시계열 없음)
 *   - 본격적인 venue별 분석은 KIS API 같은 정식 경로로 갈아탄 후가 자연스러움
 * true로 돌리면 즉시 탭 + 차트가 부활. 한 줄짜리 feature flag로 유지.
 */
const NXT_UI_VISIBLE = false;

const VENUE_TABS: { value: ChartVenue; label: string; hint: string }[] = [
  { value: "KRX", label: "KRX", hint: "정규장 09:00-15:30" },
  { value: "NXT", label: "NXT", hint: "넥스트레이드 08:00-20:00" },
  { value: "COMBINED", label: "통합", hint: "KRX + NXT 오버레이" },
];

const VENUE_STORAGE_KEY = "stock-advisor:chart-venue";

function loadVenue(): ChartVenue {
  if (!NXT_UI_VISIBLE) return "KRX";
  if (typeof window === "undefined") return "KRX";
  const v = window.localStorage.getItem(VENUE_STORAGE_KEY);
  if (v === "KRX" || v === "NXT" || v === "COMBINED") return v;
  return "KRX";
}

function parseSymbol(value: string | null): { exchange: string; symbol: string } {
  const raw = (value ?? DEFAULT_SELECTED).trim();
  const [exchange, symbol] = raw.split(":");
  if (!exchange || !symbol) return { exchange: "KR", symbol: "005930" };
  return { exchange: exchange.toUpperCase(), symbol };
}

function PageBody() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selected = useMemo(
    () => parseSymbol(searchParams.get("symbol")),
    [searchParams],
  );

  // Chart venue tab — KRX (정규장) / NXT (넥스트레이드) / 통합 (오버레이).
  // SSR-safe default; hydrate from localStorage post-mount so the user's
  // last choice persists across reloads.
  const [venue, setVenue] = useState<ChartVenue>("KRX");
  useEffect(() => {
    const stored = loadVenue();
    if (stored !== "KRX") setVenue(stored);
  }, []);
  const handleVenueChange = useCallback((next: ChartVenue) => {
    setVenue(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(VENUE_STORAGE_KEY, next);
    }
  }, []);

  const handleSelect = useCallback(
    (inst: { exchange: string; symbol: string; name: string | null }) => {
      const next = `${inst.exchange}:${inst.symbol}`;
      router.replace(`/?symbol=${next}`);
    },
    [router],
  );

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto flex max-w-[1800px] flex-col gap-4 p-3 lg:flex-row lg:gap-6 lg:p-6">
        <div className="w-full lg:w-64 lg:shrink-0">
          <div className="rounded-lg bg-white p-4 shadow lg:sticky lg:top-6">
            <WatchlistPanel
              selected={selected}
              onSelect={(inst) =>
                handleSelect({
                  exchange: inst.exchange,
                  symbol: inst.symbol,
                  name: inst.name,
                })
              }
            />
          </div>
        </div>

        <main className="min-w-0 flex-1">
          <header className="mb-4 flex flex-wrap items-baseline gap-4">
            <h1 className="text-2xl font-bold text-gray-900">
              {selected.exchange}:{selected.symbol}
            </h1>
            <p className="text-sm text-gray-600">
              {selected.exchange === "KR" ? "한국 주식" : "주식"} · 1년 일봉 + 실시간 연동
            </p>
            <Link
              href="/discovery"
              className="ml-auto rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              종목 발굴 →
            </Link>
          </header>

          {NXT_UI_VISIBLE && (
            <div className="mb-2 flex items-center gap-1.5 text-sm">
              {VENUE_TABS.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => handleVenueChange(t.value)}
                  title={t.hint}
                  className={
                    "rounded px-3 py-1 transition-colors " +
                    (venue === t.value
                      ? "bg-blue-600 text-white"
                      : "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50")
                  }
                >
                  {t.label}
                </button>
              ))}
              <span className="ml-2 text-xs text-gray-400">
                {VENUE_TABS.find((t) => t.value === venue)?.hint}
              </span>
            </div>
          )}

          {/* venue is part of the key so changing tabs cleanly remounts the
              chart instead of relying on prop-change re-renders to swap series. */}
          <PriceChart
            key={`chart:${selected.exchange}:${selected.symbol}:${venue}`}
            exchange={selected.exchange}
            symbol={selected.symbol}
            venue={venue}
          />

          <div className="mt-4">
            <AlertsPanel
              key={`alerts:${selected.exchange}:${selected.symbol}`}
              exchange={selected.exchange}
              symbol={selected.symbol}
            />
          </div>

          <div className="mt-4">
            <MarketFeedPanel
              key={`feed:${selected.exchange}:${selected.symbol}`}
              exchange={selected.exchange}
              symbol={selected.symbol}
            />
          </div>
        </main>

        <aside className="w-full lg:w-[420px] lg:shrink-0">
          <div className="lg:sticky lg:top-6">
            <ChatPanel
              key={`chat:${selected.exchange}:${selected.symbol}`}
              exchange={selected.exchange}
              symbol={selected.symbol}
            />
          </div>
        </aside>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-gray-500">불러오는 중...</div>}>
      <PageBody />
    </Suspense>
  );
}
