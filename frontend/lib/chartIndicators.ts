/**
 * Indicator catalog — the single source of truth for "what indicators
 * exist + how they're rendered + what the user can toggle".
 *
 * Each entry is consumed by both:
 *   - ChartSettings (renders the checkbox label + group)
 *   - PriceChart (computes the series + adds it via lightweight-charts)
 *
 * Adding a new indicator: append an entry here, then handle the `id` in
 * the PriceChart's switch that maps id → series factory.
 */

export type IndicatorId =
  | "sma_5"
  | "sma_20"
  | "sma_60"
  | "sma_120"
  | "ema_12"
  | "ema_26"
  | "bb_20_2"
  | "rsi_14";

export type IndicatorGroup =
  | "이동평균"
  | "지수이동평균"
  | "볼린저밴드"
  | "오실레이터";

export interface IndicatorMeta {
  id: IndicatorId;
  label: string;
  group: IndicatorGroup;
  color: string; // primary line color (also used for swatch in settings)
  pane: 0 | 1;   // 0 = price overlay, 1 = below
}

export const INDICATORS: readonly IndicatorMeta[] = [
  { id: "sma_5",   label: "SMA 5",   group: "이동평균", color: "#a855f7", pane: 0 },
  { id: "sma_20",  label: "SMA 20",  group: "이동평균", color: "#f59e0b", pane: 0 },
  { id: "sma_60",  label: "SMA 60",  group: "이동평균", color: "#2563eb", pane: 0 },
  { id: "sma_120", label: "SMA 120", group: "이동평균", color: "#6b7280", pane: 0 },

  { id: "ema_12",  label: "EMA 12",  group: "지수이동평균", color: "#ec4899", pane: 0 },
  { id: "ema_26",  label: "EMA 26",  group: "지수이동평균", color: "#0891b2", pane: 0 },

  { id: "bb_20_2", label: "Bollinger (20, 2σ)", group: "볼린저밴드", color: "#94a3b8", pane: 0 },

  { id: "rsi_14",  label: "RSI 14",  group: "오실레이터", color: "#7c3aed", pane: 1 },
] as const;

/** Group → indicators[]: lets ChartSettings render groups in order without
 *  recomputing on every render. */
export const INDICATOR_GROUPS: Record<IndicatorGroup, IndicatorMeta[]> =
  INDICATORS.reduce(
    (acc, ind) => {
      (acc[ind.group] ||= []).push(ind);
      return acc;
    },
    {} as Record<IndicatorGroup, IndicatorMeta[]>,
  );

export const GROUP_ORDER: IndicatorGroup[] = [
  "이동평균",
  "지수이동평균",
  "볼린저밴드",
  "오실레이터",
];

export const INDICATOR_STORAGE_KEY = "stock-advisor:chart-indicators";

/** Default selection — SMA(20) only. Keeps the chart clean on first load. */
export const DEFAULT_SELECTED: ReadonlySet<IndicatorId> = new Set(["sma_20"]);

export function loadSelected(): Set<IndicatorId> {
  if (typeof window === "undefined") return new Set(DEFAULT_SELECTED);
  try {
    const raw = window.localStorage.getItem(INDICATOR_STORAGE_KEY);
    if (!raw) return new Set(DEFAULT_SELECTED);
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set(DEFAULT_SELECTED);
    const valid = parsed.filter((v): v is IndicatorId =>
      INDICATORS.some((ind) => ind.id === v),
    );
    return new Set(valid);
  } catch {
    return new Set(DEFAULT_SELECTED);
  }
}

export function saveSelected(selected: Set<IndicatorId>): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    INDICATOR_STORAGE_KEY,
    JSON.stringify(Array.from(selected)),
  );
}
