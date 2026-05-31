/**
 * Type-safe wrappers over the backend REST API.
 *
 * Mirrors backend Pydantic schemas. Update both sides together if the API
 * contract changes.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------- Types (match backend/app/schemas/*) ----------

export interface InstrumentSummary {
  id: number;
  exchange: string;
  symbol: string;
  market: string | null;
  name: string | null;
  country: string;
  currency: string;
}

export interface WatchlistItem {
  id: number;
  position: number;
  added_at: string;
  instrument: InstrumentSummary;
}

export interface PriceBar {
  time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
}

export interface PriceSeriesResponse {
  instrument: string;
  interval: string;
  bars: PriceBar[];
}

export interface Tick {
  exchange: string;
  symbol: string;
  ts: string;
  close: string;
  volume_cum: number;
}

export interface LLMModelInfo {
  provider: string;
  model_id: string;
  display_name: string;
  tier: string;
  key: string; // "{provider}:{model_id}"
}

export interface LLMModelsResponse {
  models: LLMModelInfo[];
  default: {
    provider: string;
    model_id: string;
    key: string;
    available: boolean;
  };
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequest {
  exchange: string;
  symbol: string;
  question: string;
  session_id?: number | null;
  history?: ChatTurn[] | null;
  provider?: string;
  model?: string;
}

export interface ChatResponse {
  answer: string;
  instrument: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  context_preview: string;
  session_id: number | null;
}

export interface ChatSessionSummary {
  id: number;
  instrument_id: number;
  instrument: string; // "EX:SYM"
  title: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionListResponse {
  count: number;
  items: ChatSessionSummary[];
}

export interface ChatMessageRecord {
  id: number;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  created_at: string;
}

export interface ChatSessionDetailResponse {
  session: ChatSessionSummary;
  messages: ChatMessageRecord[];
}

// ---------- Fetch helpers ----------

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ---------- Instruments ----------

export function searchInstruments(params: {
  q?: string;
  market?: string;
  exchange?: string;
  limit?: number;
}): Promise<InstrumentSummary[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.market) qs.set("market", params.market);
  if (params.exchange) qs.set("exchange", params.exchange);
  if (params.limit) qs.set("limit", String(params.limit));
  return http<InstrumentSummary[]>(`/instruments?${qs.toString()}`);
}

// ---------- Watchlist ----------

export function listWatchlist(): Promise<WatchlistItem[]> {
  return http<WatchlistItem[]>("/watchlist");
}

export function addToWatchlist(args: {
  exchange: string;
  symbol: string;
  position?: number;
}): Promise<WatchlistItem> {
  return http<WatchlistItem>("/watchlist", {
    method: "POST",
    body: JSON.stringify(args),
  });
}

export function removeFromWatchlist(id: number): Promise<void> {
  return http<void>(`/watchlist/${id}`, { method: "DELETE" });
}

// ---------- Prices ----------

export function getPrices(args: {
  exchange: string;
  symbol: string;
  days?: number;
  interval?: string;
}): Promise<PriceSeriesResponse> {
  const qs = new URLSearchParams();
  if (args.days !== undefined) qs.set("days", String(args.days));
  if (args.interval) qs.set("interval", args.interval);
  return http<PriceSeriesResponse>(
    `/prices/${args.exchange}/${args.symbol}?${qs.toString()}`,
  );
}

export function wsPriceUrl(exchange: string, symbol: string): string {
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/ws/prices/${exchange}/${symbol}`;
}

// ---------- LLM ----------

export function getLLMModels(): Promise<LLMModelsResponse> {
  return http<LLMModelsResponse>("/llm/models");
}

export function chat(body: ChatRequest): Promise<ChatResponse> {
  return http<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------- Chat sessions ----------

export function listChatSessions(args: {
  exchange?: string;
  symbol?: string;
  limit?: number;
}): Promise<ChatSessionListResponse> {
  const qs = new URLSearchParams();
  if (args.exchange) qs.set("exchange", args.exchange);
  if (args.symbol) qs.set("symbol", args.symbol);
  if (args.limit !== undefined) qs.set("limit", String(args.limit));
  const q = qs.toString();
  return http<ChatSessionListResponse>(`/chat/sessions${q ? `?${q}` : ""}`);
}

export function createChatSession(args: {
  exchange: string;
  symbol: string;
  title?: string | null;
}): Promise<ChatSessionSummary> {
  return http<ChatSessionSummary>("/chat/sessions", {
    method: "POST",
    body: JSON.stringify(args),
  });
}

export function getChatSession(id: number): Promise<ChatSessionDetailResponse> {
  return http<ChatSessionDetailResponse>(`/chat/sessions/${id}`);
}

export function deleteChatSession(id: number): Promise<void> {
  return http<void>(`/chat/sessions/${id}`, { method: "DELETE" });
}

// ---------- Alerts ----------

export type AlertConditionType =
  | "price_above"
  | "price_below"
  | "pct_change_above"
  | "pct_change_below";

export interface AlertRule {
  id: number;
  instrument_id: number;
  instrument: string;
  name: string | null;
  condition_type: AlertConditionType;
  threshold: string; // Decimal serialized as string
  enabled: boolean;
  cooldown_minutes: number;
  market_hours_only: boolean;
  last_triggered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AlertRuleListResponse {
  count: number;
  items: AlertRule[];
}

export interface AlertRuleCreateRequest {
  exchange: string;
  symbol: string;
  name?: string | null;
  condition_type: AlertConditionType;
  threshold: number;
  cooldown_minutes?: number;
  market_hours_only?: boolean;
}

export function listAlertRules(args: {
  exchange?: string;
  symbol?: string;
}): Promise<AlertRuleListResponse> {
  const qs = new URLSearchParams();
  if (args.exchange) qs.set("exchange", args.exchange);
  if (args.symbol) qs.set("symbol", args.symbol);
  const q = qs.toString();
  return http<AlertRuleListResponse>(`/alerts${q ? `?${q}` : ""}`);
}

export function createAlertRule(
  body: AlertRuleCreateRequest,
): Promise<AlertRule> {
  return http<AlertRule>("/alerts", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function patchAlertRule(
  id: number,
  body: { enabled: boolean },
): Promise<AlertRule> {
  return http<AlertRule>(`/alerts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteAlertRule(id: number): Promise<void> {
  return http<void>(`/alerts/${id}`, { method: "DELETE" });
}

// ---------- Disclosures ----------

export interface DisclosureItem {
  id: number;
  source: string;
  source_id: string;
  title: string;
  filed_at: string;
  report_type: string | null;
  submitter: string | null;
  raw_url: string | null;
}

export interface DisclosureListResponse {
  instrument: string;
  count: number;
  items: DisclosureItem[];
}

export function getDisclosures(args: {
  exchange: string;
  symbol: string;
  limit?: number;
}): Promise<DisclosureListResponse> {
  const qs = new URLSearchParams();
  if (args.limit !== undefined) qs.set("limit", String(args.limit));
  const query = qs.toString();
  return http<DisclosureListResponse>(
    `/disclosures/${args.exchange}/${args.symbol}${query ? `?${query}` : ""}`,
  );
}
