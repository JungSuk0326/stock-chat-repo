"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type DisclosureItem,
  type NewsItem,
  getDisclosures,
  getNews,
} from "@/lib/api";

interface Props {
  exchange: string;
  symbol: string;
}

type TabId = "disclosures" | "news";

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

function loadInitialTab(): TabId {
  if (typeof window === "undefined") return "disclosures";
  const saved = window.localStorage.getItem(TAB_STORAGE_KEY);
  return saved === "news" ? "news" : "disclosures";
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
  const [tab, setTab] = useState<TabId>(loadInitialTab);

  const [disclosures, setDisclosures] = useState<DisclosureItem[] | null>(null);
  const [news, setNews] = useState<NewsItem[] | null>(null);
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

  // Refresh both lists periodically. Both are owner-cheap GETs.
  useEffect(() => {
    void refreshDisclosures();
    void refreshNews();
    const id = window.setInterval(() => {
      void refreshDisclosures();
      void refreshNews();
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refreshDisclosures, refreshNews]);

  const handleTabChange = useCallback((next: TabId) => {
    setTab(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TAB_STORAGE_KEY, next);
    }
  }, []);

  const refreshActive = useCallback(() => {
    if (tab === "disclosures") void refreshDisclosures();
    else void refreshNews();
  }, [tab, refreshDisclosures, refreshNews]);

  const disclosureGroups = useMemo(
    () => groupByDate(disclosures, (d) => d.filed_at),
    [disclosures],
  );
  const newsGroups = useMemo(
    () => groupByDate(news, (n) => n.published_at),
    [news],
  );

  const items = tab === "disclosures" ? disclosures : news;

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
              : "아직 적재된 뉴스가 없습니다."}
            <br />
            워커가 {tab === "disclosures" ? "1분" : "5분"} 주기로 새로 가져옵니다.
          </p>
        ) : tab === "disclosures" ? (
          <DisclosureList groups={disclosureGroups ?? []} />
        ) : (
          <NewsList groups={newsGroups ?? []} />
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
