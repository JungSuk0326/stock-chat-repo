"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type DisclosureItem,
  getDisclosures,
} from "@/lib/api";

interface Props {
  exchange: string;
  symbol: string;
}

const DEFAULT_LIMIT = 50;
const REFRESH_INTERVAL_MS = 60_000; // matches the worker's 1-min poll cadence

const KST_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
});

function formatKstDate(iso: string): string {
  try {
    return KST_FORMATTER.format(new Date(iso));
  } catch {
    return iso.slice(5, 10);
  }
}

/**
 * 종목의 최근 공시(DART) 리스트. 1분마다 자동 새로고침해서 신규 공시가
 * 워커에 잡히는 즉시 화면에 반영되도록 한다. 제목 클릭 시 DART 뷰어로 이동.
 */
export function DisclosurePanel({ exchange, symbol }: Props) {
  const [items, setItems] = useState<DisclosureItem[] | null>(null);
  const [error, setError] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const res = await getDisclosures({ exchange, symbol, limit: DEFAULT_LIMIT });
      setItems(res.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setItems([]);
    }
  }, [exchange, symbol]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const grouped = useMemo(() => {
    if (!items) return null;
    const map = new Map<string, DisclosureItem[]>();
    for (const it of items) {
      const date = formatKstDate(it.filed_at);
      const list = map.get(date) ?? [];
      list.push(it);
      map.set(date, list);
    }
    return Array.from(map.entries()); // already date-desc since `items` are
  }, [items]);

  return (
    <div className="flex h-[640px] flex-col rounded-lg bg-white shadow">
      <div className="flex items-center gap-2 border-b border-gray-200 p-3">
        <h2 className="text-sm font-semibold text-gray-900">최근 공시 (DART)</h2>
        <span className="text-xs text-gray-400">
          {items === null ? "로딩…" : `${items.length}건`}
        </span>
        <button
          type="button"
          onClick={() => void refresh()}
          className="ml-auto rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
        >
          새로고침
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {error ? (
          <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </p>
        ) : items === null ? (
          <p className="text-center text-sm text-gray-400">불러오는 중…</p>
        ) : items.length === 0 ? (
          <p className="mt-8 text-center text-sm text-gray-400">
            아직 적재된 공시가 없습니다.
            <br />
            워커가 1분 주기로 새 공시를 가져옵니다.
          </p>
        ) : (
          <ul className="space-y-3">
            {grouped?.map(([date, rows]) => (
              <li key={date}>
                <h3 className="mb-1 text-xs font-semibold text-gray-500">
                  {date}
                </h3>
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
        )}
      </div>
    </div>
  );
}
