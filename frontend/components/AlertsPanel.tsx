"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  type AlertConditionType,
  type AlertRule,
  createAlertRule,
  deleteAlertRule,
  listAlertRules,
  patchAlertRule,
} from "@/lib/api";

interface Props {
  exchange: string;
  symbol: string;
}

const CONDITION_LABELS: Record<AlertConditionType, string> = {
  price_above: "현재가 ≥",
  price_below: "현재가 ≤",
  pct_change_above: "전일대비 ≥",
  pct_change_below: "전일대비 ≤",
};

const REFRESH_INTERVAL_MS = 30_000;

function formatThreshold(rule: AlertRule): string {
  const n = Number(rule.threshold);
  if (rule.condition_type.startsWith("price_")) {
    return `${n.toLocaleString("ko-KR")}원`;
  }
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

const KST_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function formatTriggered(iso: string | null): string {
  if (!iso) return "—";
  try {
    return KST_FORMATTER.format(new Date(iso));
  } catch {
    return iso.slice(5, 16);
  }
}

/**
 * 종목 알림 룰 관리. 평가는 워커가 1분마다 실행 — 이 패널은 룰 CRUD + 발화
 * 상태 표시 전용. Top5 (LLM tool-use)가 추가되면 같은 백엔드 API를
 * LLM이 호출하게 됨 (UI 폼은 fallback).
 */
export function AlertsPanel({ exchange, symbol }: Props) {
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [error, setError] = useState<string>("");

  // Form state
  const [conditionType, setConditionType] =
    useState<AlertConditionType>("price_above");
  const [thresholdInput, setThresholdInput] = useState<string>("");
  const [nameInput, setNameInput] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await listAlertRules({ exchange, symbol });
      setRules(res.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRules([]);
    }
  }, [exchange, symbol]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    // ChatPanel emits this after a tool-confirm so the panel reflects the
    // change immediately without waiting for the next polling tick.
    const onAlertsChanged = () => {
      void refresh();
    };
    window.addEventListener("stock-advisor:alerts-changed", onAlertsChanged);
    return () => {
      window.clearInterval(id);
      window.removeEventListener(
        "stock-advisor:alerts-changed",
        onAlertsChanged,
      );
    };
  }, [refresh]);

  const handleCreate = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const threshold = Number(thresholdInput);
      if (!Number.isFinite(threshold)) {
        setError("임계값이 숫자가 아닙니다");
        return;
      }
      setSubmitting(true);
      setError("");
      try {
        await createAlertRule({
          exchange,
          symbol,
          name: nameInput.trim() || null,
          condition_type: conditionType,
          threshold,
        });
        setThresholdInput("");
        setNameInput("");
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [exchange, symbol, conditionType, thresholdInput, nameInput, refresh],
  );

  const handleToggle = useCallback(
    async (rule: AlertRule) => {
      try {
        await patchAlertRule(rule.id, { enabled: !rule.enabled });
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  const handleDelete = useCallback(
    async (rule: AlertRule) => {
      if (!confirm(`"${rule.name || "이름 없음"}" 알림을 삭제할까요?`)) return;
      try {
        await deleteAlertRule(rule.id);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  const placeholderForThreshold = useMemo(() => {
    if (conditionType.startsWith("price_")) return "예: 320000";
    return "예: 5 (5%) / -3 (-3%)";
  }, [conditionType]);

  return (
    <div className="rounded-lg bg-white p-4 shadow">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-gray-900">알림</h2>
        <span className="text-xs text-gray-400">
          {rules === null ? "로딩…" : `${rules.length}개`}
        </span>
        <span className="ml-auto text-xs text-gray-400">
          워커가 1분마다 평가
        </span>
      </div>

      {/* Create form */}
      <form onSubmit={handleCreate} className="mb-4 space-y-2">
        <div className="flex gap-2">
          <select
            value={conditionType}
            onChange={(e) =>
              setConditionType(e.target.value as AlertConditionType)
            }
            className="rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900"
          >
            {Object.entries(CONDITION_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <input
            type="number"
            step="any"
            value={thresholdInput}
            onChange={(e) => setThresholdInput(e.target.value)}
            placeholder={placeholderForThreshold}
            className="w-32 rounded border border-gray-300 px-2 py-1 text-sm text-gray-900 placeholder:text-gray-400"
            required
          />
          <input
            type="text"
            value={nameInput}
            onChange={(e) => setNameInput(e.target.value)}
            placeholder="이름 (선택)"
            maxLength={128}
            className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm text-gray-900 placeholder:text-gray-400"
          />
          <button
            type="submit"
            disabled={submitting || !thresholdInput}
            className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            추가
          </button>
        </div>
      </form>

      {/* Error */}
      {error && (
        <p className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </p>
      )}

      {/* Rules list */}
      {rules === null ? null : rules.length === 0 ? (
        <p className="py-6 text-center text-sm text-gray-400">
          이 종목의 알림이 없습니다.
          <br />
          조건과 임계값을 설정하고 [추가]를 누르세요.
        </p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {rules.map((r) => (
            <li
              key={r.id}
              className={`flex items-center gap-2 py-2 text-sm ${
                r.enabled ? "" : "opacity-50"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-gray-900">
                  {r.name || "(이름 없음)"}
                </div>
                <div className="text-xs text-gray-500">
                  {CONDITION_LABELS[r.condition_type]} {formatThreshold(r)}
                  {" · "}
                  cooldown {r.cooldown_minutes}분
                  {r.last_triggered_at && (
                    <> · 마지막 발화 {formatTriggered(r.last_triggered_at)}</>
                  )}
                </div>
              </div>
              <button
                type="button"
                onClick={() => void handleToggle(r)}
                className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
              >
                {r.enabled ? "끄기" : "켜기"}
              </button>
              <button
                type="button"
                onClick={() => void handleDelete(r)}
                className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-red-50 hover:text-red-700"
              >
                삭제
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
