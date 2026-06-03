"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo } from "react";

import { AlertsPanel } from "@/components/AlertsPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { MarketFeedPanel } from "@/components/MarketFeedPanel";
import { PriceChart } from "@/components/PriceChart";
import { WatchlistPanel } from "@/components/WatchlistPanel";

const DEFAULT_SELECTED = "KR:005930";

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

  const handleSelect = useCallback(
    (inst: { exchange: string; symbol: string; name: string | null }) => {
      const next = `${inst.exchange}:${inst.symbol}`;
      router.replace(`/?symbol=${next}`);
    },
    [router],
  );

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto flex max-w-[1800px] gap-6 p-6">
        <div className="w-64 shrink-0">
          <div className="sticky top-6 rounded-lg bg-white p-4 shadow">
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
          <header className="mb-4 flex items-baseline gap-4">
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

          <PriceChart
            key={`chart:${selected.exchange}:${selected.symbol}`}
            exchange={selected.exchange}
            symbol={selected.symbol}
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

        <aside className="w-[420px] shrink-0">
          <div className="sticky top-6">
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
