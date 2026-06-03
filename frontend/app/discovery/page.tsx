"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type Candidate,
  type Screener,
  type ScreenerCriterion,
  createScreener,
  deleteScreener,
  dismissCandidate,
  listCandidates,
  listScreeners,
  patchScreener,
  promoteCandidate,
  runScreener,
  snoozeCandidate,
} from "@/lib/api";
import { DiscoveryNaturalLanguage } from "@/components/DiscoveryNaturalLanguage";
import {
  CRITERION_CATALOG,
  type CriterionDef,
  type CriterionGroup,
  UNIVERSE_OPTIONS,
  criterionLabel,
  findCriterion,
} from "@/lib/criterionCatalog";

type StatusFilter = "active" | "new" | "snoozed" | "promoted" | "dismissed" | "all";

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "active", label: "검토 필요" },
  { value: "new", label: "신규" },
  { value: "snoozed", label: "스누즈" },
  { value: "promoted", label: "관심추가됨" },
  { value: "dismissed", label: "폐기" },
  { value: "all", label: "전체" },
];

const KST_DATE = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
function formatKst(iso: string | null): string {
  if (!iso) return "—";
  try {
    return KST_DATE.format(new Date(iso));
  } catch {
    return iso.slice(5, 16);
  }
}

export default function DiscoveryPage() {
  const [screeners, setScreeners] = useState<Screener[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("active");
  const [sourceFilter, setSourceFilter] = useState<number | null>(null);
  const [error, setError] = useState<string>("");

  const refreshScreeners = useCallback(async () => {
    try {
      const res = await listScreeners();
      setScreeners(res.items);
      if (selectedId === null && res.items.length > 0) {
        setSelectedId(res.items[0].id);
      }
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setScreeners([]);
    }
  }, [selectedId]);

  const refreshCandidates = useCallback(async () => {
    try {
      const res = await listCandidates({
        status: statusFilter === "all" ? undefined : statusFilter,
        source: sourceFilter ? `screener:${sourceFilter}` : undefined,
        limit: 200,
      });
      setCandidates(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCandidates([]);
    }
  }, [statusFilter, sourceFilter]);

  useEffect(() => {
    void refreshScreeners();
  }, [refreshScreeners]);

  useEffect(() => {
    void refreshCandidates();
  }, [refreshCandidates]);

  const selectedScreener = useMemo(
    () => screeners?.find((s) => s.id === selectedId) ?? null,
    [screeners, selectedId],
  );

  const handleCreate = useCallback(async () => {
    try {
      const created = await createScreener({
        name: "새 스크리너",
        universe: { market: "KOSPI" },
        criteria: [],
        enabled: true,
      });
      await refreshScreeners();
      setSelectedId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [refreshScreeners]);

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-[1600px] p-6">
        <header className="mb-6 flex items-center gap-4">
          <Link
            href="/"
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          >
            ← 메인
          </Link>
          <h1 className="text-2xl font-bold text-gray-900">종목 발굴</h1>
          <p className="text-sm text-gray-500">
            자연어 발굴 (AI) · 스크리너 정의 (룰 기반) — 둘 다 사용 가능
          </p>
        </header>

        {error && (
          <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* AI 자연어 발굴 — 투자자 수급 기반. 즉답형, 발굴 후 바로
            관심종목에 추가 가능. 룰 기반 screener와 독립. */}
        <div className="mb-6">
          <DiscoveryNaturalLanguage />
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[420px_1fr]">
          {/* Left column: screeners */}
          <div className="space-y-4">
            <div className="rounded-lg bg-white p-4 shadow">
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-sm font-semibold text-gray-900">스크리너</h2>
                <span className="text-xs text-gray-400">
                  {screeners === null ? "로딩…" : `${screeners.length}개`}
                </span>
                <button
                  type="button"
                  onClick={handleCreate}
                  className="ml-auto rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
                >
                  + 새로 만들기
                </button>
              </div>

              {screeners === null ? null : screeners.length === 0 ? (
                <p className="py-6 text-center text-sm text-gray-400">
                  스크리너가 없습니다.
                  <br />[+ 새로 만들기]로 시작하세요.
                </p>
              ) : (
                <ul className="space-y-1">
                  {screeners.map((s) => (
                    <li key={s.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(s.id)}
                        className={`w-full rounded px-3 py-2 text-left text-sm ${
                          s.id === selectedId
                            ? "bg-blue-50 text-blue-900 ring-1 ring-blue-200"
                            : "hover:bg-gray-50"
                        }`}
                      >
                        <div className="flex items-baseline gap-2">
                          <span className="font-medium">{s.name}</span>
                          {!s.enabled && (
                            <span className="text-xs text-gray-400">(꺼짐)</span>
                          )}
                          <span className="ml-auto text-xs text-gray-500">
                            후보 {s.candidate_count ?? 0}
                          </span>
                        </div>
                        <div className="mt-0.5 text-xs text-gray-500">
                          {s.criteria.length}개 조건 · 마지막 실행 {formatKst(s.last_run_at)}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {selectedScreener && (
              <ScreenerEditor
                key={selectedScreener.id}
                screener={selectedScreener}
                onSaved={refreshScreeners}
                onDeleted={() => {
                  setSelectedId(null);
                  void refreshScreeners();
                  void refreshCandidates();
                }}
                onRan={() => {
                  void refreshScreeners();
                  void refreshCandidates();
                }}
              />
            )}
          </div>

          {/* Right column: candidates */}
          <div className="rounded-lg bg-white p-4 shadow">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-gray-900">후보</h2>
              <span className="text-xs text-gray-400">
                {candidates === null ? "로딩…" : `${candidates.length}개`}
              </span>

              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                className="ml-2 rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900"
              >
                {STATUS_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>

              <select
                value={sourceFilter ?? ""}
                onChange={(e) =>
                  setSourceFilter(e.target.value ? Number(e.target.value) : null)
                }
                disabled={!screeners || screeners.length === 0}
                className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900 disabled:bg-gray-50"
              >
                <option value="">모든 스크리너</option>
                {screeners?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            <CandidateList
              items={candidates}
              onChanged={() => {
                void refreshCandidates();
                void refreshScreeners(); // candidate_count 갱신
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ----- ScreenerEditor -----

function ScreenerEditor({
  screener,
  onSaved,
  onDeleted,
  onRan,
}: {
  screener: Screener;
  onSaved: () => Promise<void> | void;
  onDeleted: () => void;
  onRan: () => void;
}) {
  const [name, setName] = useState(screener.name);
  const [description, setDescription] = useState(screener.description ?? "");
  const [market, setMarket] = useState<string>(screener.universe.market ?? "");
  const [criteria, setCriteria] = useState<ScreenerCriterion[]>(
    screener.criteria,
  );
  const [enabled, setEnabled] = useState(screener.enabled);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [feedback, setFeedback] = useState<string>("");

  const handleSave = async () => {
    setSaving(true);
    setFeedback("");
    try {
      await patchScreener(screener.id, {
        name: name.trim() || "이름 없음",
        description: description.trim() || null,
        universe: market ? { market } : {},
        criteria,
        enabled,
      });
      await onSaved();
      setFeedback("저장됨");
      setTimeout(() => setFeedback(""), 2000);
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    setRunning(true);
    setFeedback("");
    try {
      const res = await runScreener(screener.id);
      setFeedback(`실행 완료 — 새 후보 ${res.new_candidates}건`);
      onRan();
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    try {
      await deleteScreener(screener.id);
      onDeleted();
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="rounded-lg bg-white p-4 shadow">
      <h3 className="mb-3 text-sm font-semibold text-gray-900">편집</h3>

      <div className="space-y-3 text-sm">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            이름
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={128}
            className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-gray-900"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            설명 (선택)
          </label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={1000}
            className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-gray-900"
            placeholder="이 스크리너의 의도"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            Universe
          </label>
          <select
            value={market}
            onChange={(e) => setMarket(e.target.value)}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900"
          >
            {UNIVERSE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <CriteriaBuilder criteria={criteria} onChange={setCriteria} />

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          활성 (17:30 KST 일일 cron에서 실행)
        </label>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "저장 중…" : "저장"}
        </button>
        <button
          type="button"
          onClick={handleRun}
          disabled={running}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          title="저장된 조건으로 지금 평가 (저장 안 된 변경은 반영 안 됨)"
        >
          {running ? "실행 중…" : "지금 실행"}
        </button>
        <button
          type="button"
          onClick={handleDelete}
          className={`ml-auto rounded border px-3 py-1.5 text-xs ${
            confirmDelete
              ? "border-red-500 bg-red-50 text-red-700"
              : "border-gray-300 text-gray-700 hover:bg-red-50 hover:text-red-700"
          }`}
        >
          {confirmDelete ? "정말 삭제?" : "삭제"}
        </button>
      </div>

      {feedback && (
        <p className="mt-2 text-xs text-gray-600">{feedback}</p>
      )}
    </div>
  );
}

// ----- CriteriaBuilder -----

function CriteriaBuilder({
  criteria,
  onChange,
}: {
  criteria: ScreenerCriterion[];
  onChange: (next: ScreenerCriterion[]) => void;
}) {
  const grouped: Record<CriterionGroup, CriterionDef[]> = useMemo(() => {
    const map: Record<CriterionGroup, CriterionDef[]> = {
      기술적: [],
      재무적: [],
    };
    for (const c of CRITERION_CATALOG) map[c.group].push(c);
    return map;
  }, []);

  const addCriterion = () => {
    onChange([
      ...criteria,
      { type: "technical:rsi_below", value: 30 },
    ]);
  };

  const updateAt = (idx: number, patch: Partial<ScreenerCriterion>) => {
    const next = [...criteria];
    next[idx] = { ...next[idx], ...patch };
    onChange(next);
  };

  const removeAt = (idx: number) => {
    onChange(criteria.filter((_, i) => i !== idx));
  };

  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-gray-500">
        조건 (모두 AND)
      </label>
      {criteria.length === 0 && (
        <p className="mb-2 text-xs text-gray-400">
          조건이 없으면 universe 전체가 후보 — 보통 1개 이상 필요합니다.
        </p>
      )}
      <ul className="space-y-1.5">
        {criteria.map((c, idx) => {
          const def = findCriterion(c.type);
          return (
            <li key={idx} className="flex items-center gap-2">
              <select
                value={c.type}
                onChange={(e) => {
                  const newDef = findCriterion(e.target.value);
                  updateAt(idx, {
                    type: e.target.value,
                    // boolean 필드는 value 의미 없음, 그래도 null 두면 백엔드 가드가 처리
                    value:
                      newDef?.valueKind === "boolean"
                        ? null
                        : (c.value ?? 0),
                  });
                }}
                className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900"
              >
                {(Object.keys(grouped) as CriterionGroup[]).map((g) => (
                  <optgroup key={g} label={g}>
                    {grouped[g].map((def) => (
                      <option key={def.id} value={def.id}>
                        {def.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              {def?.valueKind === "number" ? (
                <input
                  type="number"
                  step="any"
                  value={c.value as number}
                  onChange={(e) =>
                    updateAt(idx, { value: Number(e.target.value) })
                  }
                  placeholder={def.placeholder}
                  className="w-24 rounded border border-gray-300 px-2 py-1 text-xs text-gray-900"
                />
              ) : (
                <span className="text-xs text-gray-500">(값 불필요)</span>
              )}
              {def?.unit && <span className="text-xs text-gray-500">{def.unit}</span>}
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="ml-auto rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-500 hover:bg-red-50 hover:text-red-700"
                aria-label="조건 제거"
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        onClick={addCriterion}
        className="mt-2 rounded border border-dashed border-gray-300 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50"
      >
        + 조건 추가
      </button>
    </div>
  );
}

// ----- CandidateList -----

function CandidateList({
  items,
  onChanged,
}: {
  items: Candidate[] | null;
  onChanged: () => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);

  const action = useCallback(
    async (id: number, fn: () => Promise<unknown>) => {
      setBusyId(id);
      try {
        await fn();
        onChanged();
      } catch (err) {
        alert(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyId(null);
      }
    },
    [onChanged],
  );

  if (items === null) {
    return <p className="py-6 text-center text-sm text-gray-400">로딩…</p>;
  }
  if (items.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-gray-400">
        후보가 없습니다.
        <br />
        스크리너를 만들고 [지금 실행]을 눌러보세요.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-gray-100">
      {items.map((c) => (
        <li key={c.id} className="py-3">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-xs text-gray-500">{c.instrument}</span>
            <span className="font-medium text-gray-900">
              {c.instrument_name ?? "?"}
            </span>
            <StatusBadge status={c.status} />
            <span className="ml-auto text-xs text-gray-400">
              {formatKst(c.discovered_at)}
            </span>
          </div>
          {c.reason && (
            <p className="mt-1 text-sm text-gray-700">{c.reason}</p>
          )}
          <div className="mt-1.5 flex items-center gap-1.5 text-xs">
            <span className="text-gray-400">출처: {c.source}</span>
            {c.snoozed_until && (
              <span className="text-gray-400">
                · 스누즈 종료 {formatKst(c.snoozed_until)}
              </span>
            )}
            <div className="ml-auto flex gap-1.5">
              <button
                type="button"
                onClick={() => action(c.id, () => promoteCandidate(c.id))}
                disabled={busyId === c.id || c.status === "promoted"}
                className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-blue-50 hover:text-blue-700 disabled:opacity-40"
              >
                관심추가
              </button>
              <button
                type="button"
                onClick={() => action(c.id, () => snoozeCandidate(c.id, 7))}
                disabled={busyId === c.id || c.status === "promoted"}
                className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-yellow-50 hover:text-yellow-700 disabled:opacity-40"
              >
                7일 스누즈
              </button>
              <button
                type="button"
                onClick={() => action(c.id, () => dismissCandidate(c.id))}
                disabled={busyId === c.id || c.status === "dismissed"}
                className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-red-50 hover:text-red-700 disabled:opacity-40"
              >
                폐기
              </button>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function StatusBadge({ status }: { status: Candidate["status"] }) {
  const config: Record<Candidate["status"], { label: string; cls: string }> = {
    new: { label: "신규", cls: "bg-blue-50 text-blue-700" },
    snoozed: { label: "스누즈", cls: "bg-yellow-50 text-yellow-700" },
    promoted: { label: "관심추가됨", cls: "bg-green-50 text-green-700" },
    dismissed: { label: "폐기", cls: "bg-gray-100 text-gray-500" },
  };
  const c = config[status];
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs ${c.cls}`}>{c.label}</span>
  );
}
