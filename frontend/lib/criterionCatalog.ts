/**
 * Catalog of screener criterion types. Mirrors backend `app/services/discovery.py`
 * — every entry here must have a corresponding evaluator in the engine, or it
 * silently fails to match.
 *
 * Each criterion has:
 *   - id: backend type string
 *   - label: Korean UI label
 *   - group: visual grouping in the dropdown
 *   - valueKind: "number" | "boolean" — controls the value input
 *   - unit / placeholder for the input
 *
 * Order within a group is the dropdown order.
 */

export type CriterionGroup = "기술적" | "재무적";
export type ValueKind = "number" | "boolean";

export interface CriterionDef {
  id: string;
  label: string;
  group: CriterionGroup;
  valueKind: ValueKind;
  placeholder?: string;
  unit?: string;
}

export const CRITERION_CATALOG: readonly CriterionDef[] = [
  // ── 기술적 ──
  { id: "technical:rsi_below",                    label: "RSI 이하",                 group: "기술적", valueKind: "number", placeholder: "30" },
  { id: "technical:rsi_above",                    label: "RSI 이상",                 group: "기술적", valueKind: "number", placeholder: "70" },
  { id: "technical:price_near_52w_low_within_pct",  label: "52주 저점 근처 (이내 %)",  group: "기술적", valueKind: "number", placeholder: "10", unit: "%" },
  { id: "technical:price_near_52w_high_within_pct", label: "52주 고점 근처 (이내 %)",  group: "기술적", valueKind: "number", placeholder: "5", unit: "%" },
  { id: "technical:change_pct_5d_above",          label: "5일 변화율 이상",          group: "기술적", valueKind: "number", placeholder: "5", unit: "%" },
  { id: "technical:change_pct_5d_below",          label: "5일 변화율 이하",          group: "기술적", valueKind: "number", placeholder: "-5", unit: "%" },
  { id: "technical:change_pct_20d_above",         label: "20일 변화율 이상",         group: "기술적", valueKind: "number", placeholder: "10", unit: "%" },
  { id: "technical:change_pct_20d_below",         label: "20일 변화율 이하",         group: "기술적", valueKind: "number", placeholder: "-10", unit: "%" },
  { id: "technical:volume_spike_ratio_above",     label: "거래량 spike 배수 이상",   group: "기술적", valueKind: "number", placeholder: "2", unit: "배" },
  { id: "technical:price_above_sma20",            label: "현재가가 SMA20 위",        group: "기술적", valueKind: "boolean" },
  { id: "technical:price_below_sma20",            label: "현재가가 SMA20 아래",      group: "기술적", valueKind: "boolean" },

  // ── 재무적 ──
  { id: "fundamental:per_below",                  label: "PER 이하",                 group: "재무적", valueKind: "number", placeholder: "10" },
  { id: "fundamental:per_above",                  label: "PER 이상",                 group: "재무적", valueKind: "number", placeholder: "30" },
  { id: "fundamental:pbr_below",                  label: "PBR 이하",                 group: "재무적", valueKind: "number", placeholder: "1" },
  { id: "fundamental:pbr_above",                  label: "PBR 이상",                 group: "재무적", valueKind: "number", placeholder: "3" },
  { id: "fundamental:market_cap_above",           label: "시총 이상",                group: "재무적", valueKind: "number", placeholder: "1000000000000", unit: "원" },
  { id: "fundamental:market_cap_below",           label: "시총 이하",                group: "재무적", valueKind: "number", placeholder: "100000000000", unit: "원" },
  { id: "fundamental:dividend_yield_above",       label: "배당수익률 이상",          group: "재무적", valueKind: "number", placeholder: "2", unit: "%" },
] as const;

export function findCriterion(id: string): CriterionDef | undefined {
  return CRITERION_CATALOG.find((c) => c.id === id);
}

export function criterionLabel(id: string): string {
  return findCriterion(id)?.label ?? id;
}

export const UNIVERSE_OPTIONS = [
  { value: "", label: "전체 KR" },
  { value: "KOSPI", label: "KOSPI" },
  { value: "KOSDAQ", label: "KOSDAQ" },
] as const;
