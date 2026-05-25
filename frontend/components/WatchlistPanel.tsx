"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  type InstrumentSummary,
  type WatchlistItem,
  addToWatchlist,
  listWatchlist,
  removeFromWatchlist,
  searchInstruments,
} from "@/lib/api";

interface Props {
  selected: { exchange: string; symbol: string } | null;
  onSelect: (instrument: InstrumentSummary) => void;
}

export function WatchlistPanel({ selected, onSelect }: Props) {
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [error, setError] = useState<string>("");
  const [searchOpen, setSearchOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await listWatchlist();
      setItems(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setItems([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleAdd = async (inst: InstrumentSummary) => {
    try {
      await addToWatchlist({ exchange: inst.exchange, symbol: inst.symbol });
      await refresh();
      onSelect(inst);
    } catch (err) {
      // 409 already in watchlist — still select it
      if (err instanceof Error && err.message.includes("Already")) {
        onSelect(inst);
      } else {
        alert(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setSearchOpen(false);
    }
  };

  const handleRemove = async (item: WatchlistItem, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await removeFromWatchlist(item.id);
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <aside className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-600">
          관심종목
        </h2>
        <button
          onClick={() => setSearchOpen(true)}
          className="rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
        >
          + 추가
        </button>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      {items === null ? (
        <p className="text-xs text-gray-400">불러오는 중...</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-gray-400">
          비어있음. 우측 상단 "+ 추가"로 종목을 등록하세요.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((item) => {
            const isSelected =
              selected &&
              selected.exchange === item.instrument.exchange &&
              selected.symbol === item.instrument.symbol;
            return (
              <li key={item.id}>
                <button
                  onClick={() => onSelect(item.instrument)}
                  className={`group flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition ${
                    isSelected
                      ? "bg-blue-50 text-blue-900 ring-2 ring-blue-200"
                      : "hover:bg-gray-100"
                  }`}
                >
                  <div className="min-w-0">
                    <div className="truncate font-medium">
                      {item.instrument.name ?? `${item.instrument.exchange}:${item.instrument.symbol}`}
                    </div>
                    <div className="text-xs text-gray-500">
                      {item.instrument.exchange}:{item.instrument.symbol}
                      {item.instrument.market ? ` · ${item.instrument.market}` : ""}
                    </div>
                  </div>
                  <span
                    onClick={(e) => handleRemove(item, e)}
                    className="invisible rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600 group-hover:visible"
                    role="button"
                    aria-label="remove"
                  >
                    ✕
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {searchOpen && (
        <SearchModal
          onClose={() => setSearchOpen(false)}
          onPick={(inst) => void handleAdd(inst)}
        />
      )}
    </aside>
  );
}

function SearchModal({
  onClose,
  onPick,
}: {
  onClose: () => void;
  onPick: (inst: InstrumentSummary) => void;
}) {
  const [mounted, setMounted] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<InstrumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string>("");

  // SSR-safe portal: only render after mount when `document` exists.
  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (q.length === 0) {
      setResults([]);
      setErr("");
      return;
    }
    const id = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await searchInstruments({ q, limit: 20 });
        setResults(res);
        setErr("");
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!mounted) return null;

  // Portal escapes any ancestor stacking context (sticky sidebar, chart canvas
  // z-index, etc.) so the modal reliably overlays everything.
  return createPortal(
    <div
      className="fixed inset-0 z-[1000] flex items-start justify-center bg-black/40 p-4 pt-24"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b p-3">
          <input
            autoFocus
            type="text"
            placeholder="종목명 또는 코드로 검색 (예: 삼성, 005930)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-gray-200 px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:border-blue-400 focus:outline-none"
          />
        </div>
        <div className="max-h-96 overflow-y-auto">
          {loading && <p className="p-3 text-xs text-gray-400">검색 중...</p>}
          {err && <p className="p-3 text-xs text-red-600">{err}</p>}
          {!loading && results.length === 0 && query && (
            <p className="p-3 text-xs text-gray-400">결과 없음</p>
          )}
          {results.length > 0 && (
            <ul>
              {results.map((r) => (
                <li key={r.id}>
                  <button
                    onClick={() => onPick(r)}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-medium">{r.name ?? r.symbol}</div>
                      <div className="text-xs text-gray-500">
                        {r.exchange}:{r.symbol}
                        {r.market ? ` · ${r.market}` : ""}
                      </div>
                    </div>
                    <span className="text-xs text-gray-400">{r.country}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="border-t p-2 text-right">
          <button onClick={onClose} className="text-xs text-gray-500 hover:underline">
            닫기 (ESC)
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
