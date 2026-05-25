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
