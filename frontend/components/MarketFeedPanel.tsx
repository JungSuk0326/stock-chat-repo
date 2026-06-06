"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type DisclosureItem,
  type InvestorFlowItem,
  type NewsItem,
  getDisclosures,
  getInvestorFlows,
  getNews,
} from "@/lib/api";

interface Props {
  exchange: string;
  symbol: string;
}

type TabId = "disclosures" | "news" | "flows";

const DEFAULT_LIMIT = 50;
const REFRESH_INTERVAL_MS = 60_000;
const TAB_STORAGE_KEY = "stock-advisor:feed-tab";

const KST_DATE = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
});
const KST_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  hour: "2-digit",
  minute: "2-digit",
});

function formatKstDate(iso: string): string {
  try {
    return KST_DATE.format(new Date(iso));
  } catch {
    return iso.slice(5, 10);
  }
}

function formatKstTime(iso: string): string {
  try {
    return KST_TIME.format(new Date(iso));
  } catch {
    return iso.slice(11, 16);
  }
}

/**
 * Read the persisted tab choice. Called from a post-mount effect — never
 * during render — to avoid SSR/client hydration mismatch (server has no
 * localStorage and would render a different active tab than the client).
 */
function loadStoredTab(): TabId {
  if (typeof window === "undefined") return "disclosures";
  const saved = window.localStorage.getItem(TAB_STORAGE_KEY);
  if (saved === "news" || saved === "flows") return saved;
  return "disclosures";
}

const FLOW_LIMIT = 30;

function formatSignedVolume(n: number): string {
  if (n === 0) return "0";
  const sign = n > 0 ? "+" : "−";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(0)}K`;
  return `${sign}${abs.toLocaleString("ko-KR")}`;
}

/**
 * 차트 아래 종목별 정보 피드. 탭 두 개:
 *  - 공시 (DART): 워커가 1분 주기로 수집, 헤드라인만
 *  - 뉴스 (네이버 금융): 워커가 5분 주기로 수집, 헤드라인 + 언론사
 *
 * 둘 다 같은 패턴 — 60초 자동 새로고침, 날짜별 그룹핑, 제목 클릭 → 외부 링크.
 * 활성 탭은 localStorage에 영속화.
 */
export function MarketFeedPanel({ exchange, symbol }: Props) {
  // SSR-safe default; hydrate from localStorage in a post-mount effect.
  // useState lazy init that touches `window` would lose to Next.js SSR —
  // the server renders the default, hydration freezes that DOM, and the
  // active-tab underline ends up under the wrong tab → recoverable
  // hydration mismatch warning.
  const [tab, setTab] = useState<TabId>("disclosures");
  useEffect(() => {
    const stored = loadStoredTab();
    if (stored !== "disclosures") setTab(stored);
  }, []);

  const [disclosures, setDisclosures] = useState<DisclosureItem[] | null>(null);
  const [news, setNews] = useState<NewsItem[] | null>(null);
  const [flows, setFlows] = useState<InvestorFlowItem[] | null>(null);
  const [error, setError] = useState<string>("");

  const refreshDisclosures = useCallback(async () => {
    try {
      const res = await getDisclosures({ exchange, symbol, limit: DEFAULT_LIMIT });
      setDisclosures(res.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDisclosures([]);
    }
  }, [exchange, symbol]);

  const refreshNews = useCallback(async () => {
    try {
      const res = await getNews({ exchange, symbol, limit: DEFAULT_LIMIT });
      setNews(res.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setNews([]);
    }
  }, [exchange, symbol]);

  const refreshFlows = useCallback(async () => {
    try {
      const res = await getInvestorFlows({ exchange, symbol, limit: FLOW_LIMIT });
      setFlows(res.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setFlows([]);
    }
  }, [exchange, symbol]);

  // Refresh all three periodically. Cheap GETs, keeps tab-switches instant.
  useEffect(() => {
    void refreshDisclosures();
    void refreshNews();
    void refreshFlows();
    const id = window.setInterval(() => {
      void refreshDisclosures();
      void refreshNews();
      void refreshFlows();
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refreshDisclosures, refreshNews, refreshFlows]);

  const handleTabChange = useCallback((next: TabId) => {
    setTab(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TAB_STORAGE_KEY, next);
    }
  }, []);

  const refreshActive = useCallback(() => {
    if (tab === "disclosures") void refreshDisclosures();
    else if (tab === "news") void refreshNews();
    else void refreshFlows();
  }, [tab, refreshDisclosures, refreshNews, refreshFlows]);

  const disclosureGroups = useMemo(
    () => groupByDate(disclosures, (d) => d.filed_at),
    [disclosures],
  );
  const newsGroups = useMemo(
    () => groupByDate(news, (n) => n.published_at),
    [news],
  );

  const items =
    tab === "disclosures" ? disclosures : tab === "news" ? news : flows;

  return (
    <div className="flex h-[640px] flex-col rounded-lg bg-white shadow">
      {/* Tabs */}
      <div className="flex items-center border-b border-gray-200">
        <TabButton
          active={tab === "disclosures"}
          onClick={() => handleTabChange("disclosures")}
          label="공시"
          subLabel="DART"
          count={disclosures?.length ?? null}
        />
        <TabButton
          active={tab === "news"}
          onClick={() => handleTabChange("news")}
          label="뉴스"
          subLabel="네이버"
          count={news?.length ?? null}
        />
        <TabButton
          active={tab === "flows"}
          onClick={() => handleTabChange("flows")}
          label="수급"
          subLabel="외국인·기관"
          count={flows?.length ?? null}
        />
        <button
          type="button"
          onClick={refreshActive}
          className="ml-auto mr-3 rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
        >
          새로고침
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {error ? (
          <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </p>
        ) : items === null ? (
          <p className="text-center text-sm text-gray-400">불러오는 중…</p>
        ) : items.length === 0 ? (
          <p className="mt-8 text-center text-sm text-gray-400">
            {tab === "disclosures"
              ? "아직 적재된 공시가 없습니다."
              : tab === "news"
                ? "아직 적재된 뉴스가 없습니다."
                : "아직 적재된 수급 데이터가 없습니다."}
            <br />
            워커가 {tab === "disclosures" ? "1분" : tab === "news" ? "5분" : "매일 16:30 KST"}
            {tab === "flows" ? " 에 새로 가져옵니다." : " 주기로 새로 가져옵니다."}
          </p>
        ) : tab === "disclosures" ? (
          <DisclosureList groups={disclosureGroups ?? []} />
        ) : tab === "news" ? (
          <NewsList groups={newsGroups ?? []} />
        ) : (
          <FlowList items={flows ?? []} />
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
  subLabel,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  subLabel: string;
  count: number | null;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative flex items-baseline gap-1.5 px-4 py-3 text-sm font-medium ${
        active ? "text-gray-900" : "text-gray-500 hover:text-gray-700"
      }`}
    >
      <span>{label}</span>
      <span className="text-xs text-gray-400">{subLabel}</span>
      {count !== null && (
        <span
          className={`ml-1 rounded-full px-1.5 text-xs ${
            active ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-500"
          }`}
        >
          {count}
        </span>
      )}
      {active && (
        <span className="absolute inset-x-3 -bottom-px h-0.5 bg-blue-600" />
      )}
    </button>
  );
}

function groupByDate<T>(
  items: T[] | null,
  getIso: (item: T) => string,
): [string, T[]][] | null {
  if (!items) return null;
  const map = new Map<string, T[]>();
  for (const it of items) {
    const date = formatKstDate(getIso(it));
    const list = map.get(date) ?? [];
    list.push(it);
    map.set(date, list);
  }
  return Array.from(map.entries());
}

function DisclosureList({ groups }: { groups: [string, DisclosureItem[]][] }) {
  return (
    <ul className="space-y-3">
      {groups.map(([date, rows]) => (
        <li key={date}>
          <h3 className="mb-1 text-xs font-semibold text-gray-500">{date}</h3>
          <ul className="space-y-1">
            {rows.map((it) => (
              <li key={it.id} className="text-sm leading-snug">
                {it.raw_url ? (
                  <a
                    href={it.raw_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gray-900 hover:text-blue-700 hover:underline"
                  >
                    {it.title}
                  </a>
                ) : (
                  <span className="text-gray-900">{it.title}</span>
                )}
                {it.submitter && (
                  <span className="ml-1 text-xs text-gray-500">
                    · {it.submitter}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/**
 * 일별 외국인/기관/개인 순매수 + 외국인 보유율 + 종가. 빨강=매수, 파랑=매도
 * (한국 차트 관례). 거래일 한 줄당 한 행 — 모든 정보 한눈에.
 */
function FlowList({ items }: { items: InvestorFlowItem[] }) {
  return (
    <table className="w-full text-xs">
      <thead className="text-gray-500">
        {/* sticky on <th> + bg-white로 스크롤 시 행이 비치지 않게.
            box-shadow로 헤더 아래 구분선 유지 (border는 sticky 시 끊김) */}
        <tr>
          <th className="sticky top-0 z-10 bg-white py-1.5 text-left font-medium shadow-[0_1px_0_0_#e5e7eb]">날짜</th>
          <th className="sticky top-0 z-10 bg-white py-1.5 text-right font-medium shadow-[0_1px_0_0_#e5e7eb]">외국인</th>
          <th className="sticky top-0 z-10 bg-white py-1.5 text-right font-medium shadow-[0_1px_0_0_#e5e7eb]">기관</th>
          <th className="sticky top-0 z-10 bg-white py-1.5 text-right font-medium shadow-[0_1px_0_0_#e5e7eb]">개인</th>
          <th className="sticky top-0 z-10 bg-white py-1.5 text-right font-medium shadow-[0_1px_0_0_#e5e7eb]">보유율</th>
          <th className="sticky top-0 z-10 bg-white py-1.5 text-right font-medium shadow-[0_1px_0_0_#e5e7eb]">종가</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => (
          <tr key={it.id} className="border-b border-gray-100">
            <td className="py-1.5 font-mono text-gray-700">
              {it.trade_date.slice(5)}
            </td>
            <td className={`py-1.5 text-right font-mono ${flowColor(it.foreign_net_volume)}`}>
              {formatSignedVolume(it.foreign_net_volume)}
            </td>
            <td className={`py-1.5 text-right font-mono ${flowColor(it.institutional_net_volume)}`}>
              {formatSignedVolume(it.institutional_net_volume)}
            </td>
            <td className={`py-1.5 text-right font-mono ${flowColor(it.individual_net_volume)}`}>
              {formatSignedVolume(it.individual_net_volume)}
            </td>
            <td className="py-1.5 text-right font-mono text-gray-600">
              {it.foreign_hold_ratio ? `${it.foreign_hold_ratio}%` : "—"}
            </td>
            <td className="py-1.5 text-right font-mono text-gray-700">
              {it.close_price ? it.close_price.toLocaleString("ko-KR") : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function flowColor(n: number): string {
  if (n > 0) return "text-red-600";   // 한국 관례: 매수=빨강
  if (n < 0) return "text-blue-600";  // 매도=파랑
  return "text-gray-400";
}

function NewsList({ groups }: { groups: [string, NewsItem[]][] }) {
  return (
    <ul className="space-y-3">
      {groups.map(([date, rows]) => (
        <li key={date}>
          <h3 className="mb-1 text-xs font-semibold text-gray-500">{date}</h3>
          <ul className="space-y-1">
            {rows.map((it) => (
              <li key={it.id} className="text-sm leading-snug">
                <span className="mr-1 text-xs text-gray-400">
                  {formatKstTime(it.published_at)}
                </span>
                <a
                  href={it.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-gray-900 hover:text-blue-700 hover:underline"
                >
                  {it.title}
                </a>
                {it.publisher && (
                  <span className="ml-1 text-xs text-gray-500">
                    · {it.publisher}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
