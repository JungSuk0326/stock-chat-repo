"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type ChatTurn,
  type LLMModelInfo,
  chat,
  getLLMModels,
} from "@/lib/api";

interface Props {
  exchange: string;
  symbol: string;
}

const MODEL_STORAGE_KEY = "stock-advisor:llm-model";

/**
 * 종목 컨텍스트가 자동 주입되는 LLM 상담 패널.
 * - 모델은 /llm/models 응답에서 채워지고, 키가 없는 provider는 자동 제외됨.
 * - 사용자가 고른 모델은 localStorage에 저장 → 새로고침 후에도 유지.
 * - History는 컴포넌트 로컬 state. 종목 바뀌면 자동 reset (key prop 활용).
 */
export function ChatPanel({ exchange, symbol }: Props) {
  const [models, setModels] = useState<LLMModelInfo[] | null>(null);
  const [modelsError, setModelsError] = useState<string>("");
  const [selectedKey, setSelectedKey] = useState<string>("");

  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 모델 카탈로그 로드 — 마운트 1회.
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

  // 새 메시지 추가 시 자동 스크롤 to bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, loading]);

  const handleModelChange = useCallback((key: string) => {
    setSelectedKey(key);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MODEL_STORAGE_KEY, key);
    }
  }, []);

  const selectedModel = useMemo(
    () => models?.find((m) => m.key === selectedKey) ?? null,
    [models, selectedKey],
  );

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const q = question.trim();
      if (!q || loading || !selectedModel) return;

      const userTurn: ChatTurn = { role: "user", content: q };
      const nextHistory = [...history, userTurn];
      setHistory(nextHistory);
      setQuestion("");
      setLoading(true);
      setError("");

      try {
        const res = await chat({
          exchange,
          symbol,
          question: q,
          history,
          provider: selectedModel.provider,
          model: selectedModel.model_id,
        });
        setHistory([
          ...nextHistory,
          { role: "assistant", content: res.answer },
        ]);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        // 사용자 질문은 유지하되, assistant 응답 자리는 비워둠.
      } finally {
        setLoading(false);
      }
    },
    [exchange, symbol, question, history, loading, selectedModel],
  );

  const handleClear = useCallback(() => {
    setHistory([]);
    setError("");
  }, []);

  return (
    <div className="flex h-[640px] flex-col rounded-lg bg-white shadow">
      {/* Header: 모델 선택 + 히스토리 초기화 */}
      <div className="flex items-center gap-2 border-b border-gray-200 p-3">
        <span className="text-xs font-medium text-gray-500">모델</span>
        {models === null ? (
          <span className="text-xs text-gray-400">로딩…</span>
        ) : models.length === 0 ? (
          <span className="text-xs text-red-600">
            {modelsError || "사용 가능한 모델 없음 (API 키 확인)"}
          </span>
        ) : (
          <select
            value={selectedKey}
            onChange={(e) => handleModelChange(e.target.value)}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900"
          >
            {models.map((m) => (
              <option key={m.key} value={m.key}>
                {m.display_name} ({m.tier})
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={handleClear}
          disabled={history.length === 0 && !error}
          className="ml-auto rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          대화 초기화
        </button>
      </div>

      {/* History */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3">
        {history.length === 0 && !loading && !error ? (
          <p className="mt-8 text-center text-sm text-gray-400">
            {exchange}:{symbol} 종목의 시세 · 추세 · 기술적 지표가 자동으로
            컨텍스트에 들어갑니다.
            <br />
            아래에 질문을 입력해보세요.
          </p>
        ) : (
          <ul className="space-y-3">
            {history.map((turn, idx) => (
              <li
                key={idx}
                className={
                  turn.role === "user"
                    ? "ml-auto max-w-[85%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white"
                    : "mr-auto max-w-[85%] whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-900"
                }
              >
                {turn.content}
              </li>
            ))}
            {loading && (
              <li className="mr-auto max-w-[85%] rounded-lg bg-gray-100 px-3 py-2 text-sm italic text-gray-500">
                생각 중…
              </li>
            )}
            {error && (
              <li className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                요청 실패: {error}
              </li>
            )}
          </ul>
        )}
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-gray-200 p-3"
      >
        <div className="flex gap-2">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="이 종목에 대해 무엇이든 물어보세요"
            disabled={loading || !selectedModel}
            className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 disabled:bg-gray-50"
          />
          <button
            type="submit"
            disabled={loading || !question.trim() || !selectedModel}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            보내기
          </button>
        </div>
      </form>
    </div>
  );
}
