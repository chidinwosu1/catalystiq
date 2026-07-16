/**
 * Client for the Phase 1 backend (catalystiq/routers/market_data.py).
 *
 * DEV-ONLY AUTH WARNING: the backend's action endpoints take a single
 * static bearer token (ACTION_API_KEY). Reading it from VITE_ACTION_API_KEY
 * bakes it into the browser bundle, which is fine for local development but
 * is not a real auth model - a shipped build must not do this. Before any
 * real deployment this needs a proper session/BFF layer in front of the API
 * so the raw action key never reaches the browser.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ACTION_API_KEY = import.meta.env.VITE_ACTION_API_KEY ?? "";

export interface Quote {
  symbol: string;
  price: number;
  previous_close: number | null;
  as_of: string;
}

export interface OHLCVBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type DataQualityIssueType =
  | "out_of_order"
  | "duplicate_row"
  | "missing_trading_day"
  | "abnormal_gap"
  | "live_quote_mismatch"
  | "thin_history";

export interface DataQualityIssue {
  type: DataQualityIssueType;
  date: string | null;
  detail: string;
}

export interface DataQualityReport {
  symbol: string;
  passed: boolean;
  issues: DataQualityIssue[];
  checked_at: string;
  bar_count: number;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${ACTION_API_KEY}`,
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, `Could not reach the API at ${BASE_URL}. Is the backend running?`);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON - fall back to statusText
    }
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

export function getQuote(symbol: string): Promise<Quote> {
  return request(`/market-data/quote/${encodeURIComponent(symbol)}`);
}

export function getOhlcv(symbol: string, days = 365): Promise<OHLCVBar[]> {
  return request(`/market-data/ohlcv/${encodeURIComponent(symbol)}?days=${days}`);
}

export function ingestPriceHistory(symbol: string, days = 365 * 5): Promise<DataQualityReport> {
  return request(`/market-data/ingest/${encodeURIComponent(symbol)}?days=${days}`, {
    method: "POST",
  });
}
