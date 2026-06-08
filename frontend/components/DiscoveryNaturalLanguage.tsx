"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type DiscoveryCandidateOut,
  type DiscoveryLlmResponse,
  type LLMModelInfo,
  addToWatchlist,
  discoveryLlmQuery,
  getLLMModels,
} from "@/lib/api";

const EXAMPLE_QUERIES = [
  "근 1개월간 사모펀드가 가장 많이 순매수한 종목 10개",
  "최근 일주일 연기금 순매수 상위",
  "지난 한 달 외국인 순매도 상위 KOSDAQ",
  "기관이 최근 3개월간 가장 많이 산 종목",
];

// Discovery uses its own model preference — keeping it separate from
// the chat picker lets the user run Flash here (free) while keeping
// Pro/Opus on chat for quality.
const MODEL_STORAGE_KEY = "stock-advisor:discovery-model";

function formatWon(v: number): string {
  if (Math.abs(v) >= 1_0000_0000) {
    return `${(v / 1_0000_0000).toLocaleString("ko-KR", {
      maximumFractionDigits: 1,
      signDisplay: "always",
    })}억원`;
  }
  if (Math.abs(v) >= 1_0000) {
    return `${(v / 1_0000).toLocaleString("ko-KR", {
      maximumFractionDigits: 1,
      signDisplay: "always",
    })}만원`;
  }
  return `${v.toLocaleString("ko-KR", { signDisplay: "always" })}원`;
}

export function DiscoveryNaturalLanguage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<DiscoveryLlmResponse | null>(null);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<Set<string>>(new Set());

  const [models, setModels] = useState<LLMModelInfo[] | null>(null);
  const [modelsError, setModelsError] = useState<string>("");
  const [selectedKey, setSelectedKey] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await getLLMModels();
        if (cancelled) return;
        setModels(res.models);
        const saved =
          typeof window !== "undefined"
            ? window.localStorage.getItem(MODEL_STORAGE_KEY)
            : null;
        const initial =
          (saved && res.models.find((m) => m.key === saved)?.key) ??
          (res.default.available ? res.default.key : null) ??
          res.models[0]?.key ??
          "";
        setSelectedKey(initial);
      } catch (err) {
        if (cancelled) return;
        setModelsError(err instanceof Error ? err.message : String(err));
        setModels([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedModel = useMemo(
    () => models?.find((m) => m.key === selectedKey) ?? null,
    [models, selectedKey],
  );

  const handleModelChange = useCallback((key: string) => {
    setSelectedKey(key);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MODEL_STORAGE_KEY, key);
    }
  }, []);

  const handleSubmit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;
      setSubmitted(trimmed);
      setLoading(true);
      setError("");
      setResponse(null);
      setAdded(new Set());
      try {
        const res = await discoveryLlmQuery({
          query: trimmed,
          provider: selectedModel?.provider,
          model: selectedModel?.model_id,
        });
        setResponse(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [loading, selectedModel],
  );

  const handleAdd = useCallback(
    async (c: DiscoveryCandidateOut) => {
      const key = `${c.exchange}:${c.symbol}`;
      if (added.has(key)) return;
      try {
        await addToWatchlist({ exchange: c.exchange, symbol: c.symbol });
        setAdded((prev) => {
          const next = new Set(prev);
          next.add(key);
          return next;
        });
      } catch (err) {
        // Surface the error but don't lose the rest of the list.
        setError(
          `${key} 추가 실패: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [added],
  );

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            자연어 발굴 (AI)
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            예: &quot;근 1개월간 사모펀드가 가장 많이 산 종목&quot; — 투자자
            수급 기반 발굴
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs font-medium text-gray-500">모델</span>
          {models === null ? (
            <span className="text-xs text-gray-400">로딩…</span>
          ) : models.length === 0 ? (
            <span className="text-xs text-red-600">
              {modelsError || "사용 가능한 모델 없음"}
            </span>
          ) : (
            <select
              value={selectedKey}
              onChange={(e) => handleModelChange(e.target.value)}
              disabled={loading}
              className="rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 disabled:bg-gray-50"
            >
              {models.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.display_name} ({m.tier})
                </option>
              ))}
            </select>
          )}
        </div>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleSubmit(query);
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="질문을 자연어로 입력하세요"
          className="min-w-0 flex-1 rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {loading ? "조회 중..." : "발굴"}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLE_QUERIES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => {
              setQuery(ex);
              void handleSubmit(ex);
            }}
            disabled={loading}
            className="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {ex}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {submitted && (response || loading) && (
        <div className="mt-4">
          <p className="text-xs text-gray-500">질문</p>
          <p className="mt-1 rounded bg-gray-50 px-3 py-2 text-sm text-gray-800">
            {submitted}
          </p>
        </div>
      )}

      {response && (
        <>
          {response.answer && (
            <div className="mt-4">
              <p className="text-xs text-gray-500">답변</p>
              <p className="mt-1 whitespace-pre-line text-sm text-gray-800">
                {response.answer}
              </p>
            </div>
          )}

          {response.tools_called.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {response.tools_called.map((t, i) => (
                <span
                  key={i}
                  className="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600"
                  title={JSON.stringify(t.arguments)}
                >
                  {t.name}({
                    Object.entries(t.arguments)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ")
                  })
                </span>
              ))}
            </div>
          )}

          {response.candidates.length > 0 ? (
            <ul className="mt-4 divide-y divide-gray-100 rounded border border-gray-200">
              {response.candidates.map((c, i) => {
                const key = `${c.exchange}:${c.symbol}`;
                const isAdded = added.has(key);
                return (
                  <li
                    key={key}
                    className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <span className="text-xs font-medium text-gray-400">
                          {i + 1}
                        </span>
                        <span className="font-medium text-gray-900">
                          {c.name}
                        </span>
                        <span className="text-xs text-gray-500">
                          {key}
                        </span>
                      </div>
                      <p className="mt-0.5 text-xs text-gray-500">
                        {c.metric_label} ·{" "}
                        <span
                          className={
                            c.metric_value >= 0
                              ? "text-red-600"
                              : "text-blue-600"
                          }
                        >
                          {formatWon(c.metric_value)}
                        </span>
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleAdd(c)}
                      disabled={isAdded}
                      className="shrink-0 rounded border border-gray-300 px-2.5 py-1 text-xs text-gray-700 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 disabled:cursor-default disabled:border-green-200 disabled:bg-green-50 disabled:text-green-700"
                    >
                      {isAdded ? "추가됨" : "관심종목 추가"}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            !loading &&
            response.tools_called.length > 0 && (
              <p className="mt-3 text-sm text-gray-500">
                결과가 비어 있습니다. KRX 로그인 또는 데이터 누락 여부를 확인하세요.
              </p>
            )
          )}
        </>
      )}
    </section>
  );
}
