"use client";

import { useRouter, useSearchParams } from "next/navigation";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type ChatSessionSummary,
  type ChatTurn,
  type LLMModelInfo,
  chat,
  createChatSession,
  deleteChatSession,
  getChatSession,
  getLLMModels,
  listChatSessions,
} from "@/lib/api";

interface Props {
  exchange: string;
  symbol: string;
}

const MODEL_STORAGE_KEY = "stock-advisor:llm-model";

const TIME_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function sessionLabel(s: ChatSessionSummary): string {
  if (s.title && s.title.trim()) return s.title;
  try {
    return TIME_FORMATTER.format(new Date(s.updated_at));
  } catch {
    return s.updated_at.slice(5, 16);
  }
}

/**
 * LLM 상담 패널 + 세션 영속화.
 *
 * - 마운트 시 종목별 세션 리스트 fetch
 * - URL ?session=N이 있고 같은 종목이면 그 세션 활성화, 없으면 가장 최근 세션
 * - 활성 세션의 메시지를 백엔드에서 받아 history로 사용
 * - 질문 보낼 때 session_id 포함 → 새 세션이면 백엔드가 만들어 응답에 id 동봉
 * - 모델 선택은 localStorage에 영속 (이전과 동일)
 */
export function ChatPanel({ exchange, symbol }: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();

  // ----- Models -----
  const [models, setModels] = useState<LLMModelInfo[] | null>(null);
  const [modelsError, setModelsError] = useState<string>("");
  const [selectedKey, setSelectedKey] = useState<string>("");

  // ----- Sessions -----
  const [sessions, setSessions] = useState<ChatSessionSummary[] | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);

  // ----- Conversation -----
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Helper: rewrite the URL keeping ?symbol intact, optionally setting ?session.
  const updateSessionInUrl = useCallback(
    (id: number | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (id === null) {
        params.delete("session");
      } else {
        params.set("session", String(id));
      }
      const q = params.toString();
      router.replace(q ? `/?${q}` : "/");
    },
    [router, searchParams],
  );

  // ----- Load models (once on mount) -----
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

  // ----- Load sessions for the current symbol, pick active session -----
  // Re-runs only when (exchange, symbol) changes. URL ?session= changes do
  // NOT retrigger this — they go through the message loader directly.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await listChatSessions({ exchange, symbol, limit: 50 });
        if (cancelled) return;
        setSessions(res.items);

        const urlSessionStr = searchParams.get("session");
        const urlSessionId = urlSessionStr ? Number(urlSessionStr) : null;
        const urlMatch =
          urlSessionId != null && res.items.find((s) => s.id === urlSessionId)
            ? urlSessionId
            : null;

        const fallback = res.items[0]?.id ?? null;
        const next = urlMatch ?? fallback;
        setActiveSessionId(next);
        // Keep URL consistent — if URL was stale, drop it.
        if (urlSessionId != null && urlSessionId !== next) {
          updateSessionInUrl(next);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setSessions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
    // searchParams intentionally excluded — URL is read once per symbol
    // change, not on every navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange, symbol]);

  // ----- Load messages for active session -----
  useEffect(() => {
    let cancelled = false;
    if (activeSessionId == null) {
      setHistory([]);
      return;
    }
    (async () => {
      try {
        const res = await getChatSession(activeSessionId);
        if (cancelled) return;
        setHistory(
          res.messages.map((m) => ({ role: m.role, content: m.content })),
        );
        setError("");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setHistory([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  // Auto-scroll on new message / loading state.
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

  const handleSessionChange = useCallback(
    (id: number | null) => {
      setActiveSessionId(id);
      updateSessionInUrl(id);
      setConfirmingDelete(false);
    },
    [updateSessionInUrl],
  );

  const handleNewChat = useCallback(() => {
    // We just clear the active session — the actual session row is created
    // server-side on first /chat call.
    handleSessionChange(null);
  }, [handleSessionChange]);

  const handleDeleteSession = useCallback(async () => {
    if (activeSessionId == null) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    const id = activeSessionId;
    setConfirmingDelete(false);
    try {
      await deleteChatSession(id);
      const remaining = (sessions ?? []).filter((s) => s.id !== id);
      setSessions(remaining);
      handleSessionChange(remaining[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [activeSessionId, confirmingDelete, sessions, handleSessionChange]);

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
          session_id: activeSessionId,
          provider: selectedModel.provider,
          model: selectedModel.model_id,
        });
        setHistory([
          ...nextHistory,
          { role: "assistant", content: res.answer },
        ]);
        // Backend may have created a session for us.
        if (res.session_id != null && res.session_id !== activeSessionId) {
          setActiveSessionId(res.session_id);
          updateSessionInUrl(res.session_id);
        }
        // Refresh sessions list (updated_at moved + maybe new row + auto-title).
        try {
          const updated = await listChatSessions({ exchange, symbol, limit: 50 });
          setSessions(updated.items);
        } catch {
          // non-fatal — the next mount will refresh
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [
      exchange,
      symbol,
      question,
      history,
      loading,
      selectedModel,
      activeSessionId,
      updateSessionInUrl,
    ],
  );

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col rounded-lg bg-white shadow">
      {/* Header row 1: session selector + new + delete */}
      <div className="flex items-center gap-2 border-b border-gray-100 p-3">
        <select
          value={activeSessionId ?? ""}
          onChange={(e) =>
            handleSessionChange(e.target.value ? Number(e.target.value) : null)
          }
          disabled={!sessions || sessions.length === 0}
          className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 disabled:bg-gray-50"
        >
          {activeSessionId == null && (
            <option value="">(새 대화)</option>
          )}
          {sessions?.map((s) => (
            <option key={s.id} value={s.id}>
              {sessionLabel(s)} · {s.message_count}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={handleNewChat}
          className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
          title="새 대화 시작"
        >
          새 대화
        </button>
        <button
          type="button"
          onClick={handleDeleteSession}
          disabled={activeSessionId == null}
          className={`rounded border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 ${
            confirmingDelete
              ? "border-red-500 bg-red-50 text-red-700"
              : "border-gray-300 text-gray-700 hover:bg-gray-50"
          }`}
          title="현재 세션 삭제"
        >
          {confirmingDelete ? "정말?" : "삭제"}
        </button>
      </div>

      {/* Header row 2: model + clear-only-button removed (now per-session) */}
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
      </div>

      {/* History */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3">
        {history.length === 0 && !loading && !error ? (
          <p className="mt-8 text-center text-sm text-gray-400">
            {exchange}:{symbol} 종목의 시세 · 추세 · 기술적 지표 · 최근 공시가
            자동으로 컨텍스트에 들어갑니다.
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
      <form onSubmit={handleSubmit} className="border-t border-gray-200 p-3">
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
