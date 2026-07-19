/**
 * Client for the Catalyst IQ backend.
 *
 * AUTH: the primary auth is a server-side session cookie (httpOnly), set by
 * POST /auth/login and sent automatically via `credentials: "include"` - the
 * raw secret never lives in this bundle. A static bearer token is attached
 * ONLY if VITE_ACTION_API_KEY is explicitly provided (for local/programmatic
 * use); production builds should leave it unset and rely on the cookie.
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

export interface FundamentalsSnapshot {
  symbol: string;
  long_name: string | null;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  ev_to_ebitda: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  gross_margins: number | null;
  operating_margins: number | null;
  return_on_equity: number | null;
  free_cashflow: number | null;
  total_debt: number | null;
  total_cash: number | null;
  as_of: string;
}

export interface AccountInfo {
  status: string;
  currency: string;
  cash: string;
  buying_power: string;
  portfolio_value: string;
  equity: string;
  last_equity: string;
  trading_blocked: boolean;
  account_blocked: boolean;
  pattern_day_trader: boolean;
}

export interface Position {
  symbol: string;
  side: string;
  qty: string;
  avg_entry_price: string;
  market_value: string;
  cost_basis: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  current_price: string;
  change_today: string;
}

export type OrderSide = "buy" | "sell";
export type OrderType = "market" | "limit" | "stop" | "stop_limit" | "trailing_stop";
export type TimeInForce = "day" | "gtc" | "ioc" | "fok";

export interface NewOrder {
  symbol: string;
  side: OrderSide;
  type: OrderType;
  time_in_force?: TimeInForce;
  qty?: number;
  notional?: number;
  limit_price?: number;
  stop_price?: number;
  trail_percent?: number;
  trail_price?: number;
  extended_hours?: boolean;
  client_order_id?: string;
  take_profit_price?: number;
  stop_loss_price?: number;
}

// "due" = the scheduled time has passed and the order is ready for manual
// review/confirmation; it is NEVER submitted automatically (§13).
export type ScheduledOrderStatus = "pending" | "due" | "submitted" | "failed" | "cancelled";

export interface ScheduledOrderRecord {
  id: number;
  symbol: string;
  order: NewOrder;
  scheduled_at: string;
  status: ScheduledOrderStatus;
  broker_order_id: string | null;
  error_detail: string | null;
  created_at: string;
}

// --- Order confirmation (§13): two-step submit -------------------------
// Step 1 (confirm) returns the exact details to review plus a single-use,
// short-lived token bound to them. Step 2 (submit) requires that token; any
// change to the order invalidates it.

export interface OrderReview {
  symbol: string;
  side: OrderSide;
  type: OrderType;
  time_in_force: TimeInForce;
  qty: number | null;
  notional: number | null;
  limit_price: number | null;
  stop_price: number | null;
  estimated_max_loss: number | null;
  account_id: string;
  mode: string;
}

export interface OrderConfirmation {
  review: OrderReview;
  confirmation_token: string;
  expires_at: string;
}

export interface BrokerConnectionTest {
  provider: string;
  ok: boolean;
  detail: string;
}

export type IndicatorStatus = "computed" | "insufficient_data";

export interface IndicatorReading {
  name: string;
  status: IndicatorStatus;
  value: number | null;
  description: string;
  params: Record<string, number>;
  min_bars_required: number;
  percentile_5y: number | null;
  zscore_5y: number | null;
}

export interface TechnicalSnapshot {
  symbol: string;
  as_of: string;
  bars_used: number;
  history_days_available: number;
  indicators: IndicatorReading[];
  warnings: string[];
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
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  // The session cookie (credentials: "include") is the primary auth; a bearer
  // is attached only when a dev key is explicitly configured.
  if (ACTION_API_KEY) headers.Authorization = `Bearer ${ACTION_API_KEY}`;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      credentials: "include",
      headers,
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

// --- Auth (session cookie) ---------------------------------------------

export interface SessionStatus {
  authenticated: boolean;
  expires_at: string | null;
}

/** Whether the current browser session is authenticated. */
export function getSession(): Promise<SessionStatus> {
  return request("/auth/session");
}

/** Exchange a password for an httpOnly session cookie. */
export function login(password: string): Promise<SessionStatus> {
  return request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

/** Clear the session cookie. */
export function logout(): Promise<{ ok: boolean }> {
  return request("/auth/logout", { method: "POST" });
}

// --- Data sources (health / admin) -------------------------------------

export interface DataSourceSummary {
  name: string;
  domain: string;
  implemented: boolean;
  enabled: boolean;
  configured: boolean;
  requires_api_key: boolean;
  license: string;
  // Ephemeral sources (e.g. FRED) are never persisted, so ingestion/freshness
  // are intentionally absent.
  ephemeral?: boolean;
}

export interface DataSourceHealth extends DataSourceSummary {
  missing_settings: string[];
  last_successful_ingestion_at: string | null;
  last_failure_category: string | null;
  last_failure_at: string | null;
  circuit_breaker: string;
  data_freshness_at: string | null;
  note?: string;
}

export function getDataSourcesHealth(): Promise<DataSourceHealth[]> {
  return request("/data-sources/health");
}

// --- Rule-Based Macroeconomic Context (FRED, ephemeral) ----------------
// FRED data is fetched on demand, shown ephemerally with required attribution,
// and NEVER persisted or fed to any score/model/order path. The backend serves
// these responses with Cache-Control: no-store.

export interface MacroIndicatorPoint {
  date: string;
  value: number | null;
}

export interface MacroIndicator {
  series_id: string;
  title: string;
  owner: string;
  attribution: string;
  purpose: string;
  units: string;
  frequency: string;
  status: "ok" | "no_data" | "unavailable" | "pending";
  detail?: string;
  recent?: MacroIndicatorPoint[];
  latest_value?: number;
  latest_date?: string;
  prior_value?: number;
  prior_date?: string;
  change?: number;
}

export interface MacroContext {
  panel: string;
  notice: string;
  disclaimer: string;
  terms_reviewed_url: string;
  terms_reviewed_date: string;
  as_of: string | null;
  ephemeral: boolean;
  available: boolean;
  reason?: string;
  retrieved_at?: string;
  indicators: MacroIndicator[];
}

/** The ephemeral macro-context panel. Never cached; not persisted. */
export function getFredContext(): Promise<MacroContext> {
  return request("/fred/context");
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

export function getFundamentals(symbol: string): Promise<FundamentalsSnapshot> {
  return request(`/market-data/fundamentals/${encodeURIComponent(symbol)}`);
}

export function getTechnicalSnapshot(symbol: string, days = 365 * 5): Promise<TechnicalSnapshot> {
  return request(`/analysis/technical/${encodeURIComponent(symbol)}?days=${days}`);
}

export function getAccount(): Promise<AccountInfo> {
  return request("/paper/account");
}

export function getPositions(): Promise<Position[]> {
  return request("/paper/positions");
}

export function getOrders(): Promise<Record<string, unknown>[]> {
  return request("/paper/orders");
}

/**
 * Step 1 of order submission (§13): review the exact order details and
 * receive a single-use, short-lived confirmation token bound to them.
 * A 403 here means paper submission is disabled (or live is unavailable).
 */
export function confirmOrder(order: NewOrder, accountId: string): Promise<OrderConfirmation> {
  return request("/paper/orders/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order, account_id: accountId, confirmation_token: "" }),
  });
}

/**
 * Step 2 of order submission (§13): submit with the confirmation token from
 * confirmOrder(). The token is single-use and bound to these exact details -
 * any change (or expiry, or reuse) is rejected with a 403.
 */
export function submitOrder(
  order: NewOrder,
  accountId: string,
  confirmationToken: string
): Promise<Record<string, unknown>> {
  return request("/paper/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order, account_id: accountId, confirmation_token: confirmationToken }),
  });
}

/** Read-only Webull reachability check (never places/cancels an order). */
export function getBrokerConnectionTest(): Promise<BrokerConnectionTest> {
  return request("/paper/connection-test");
}

export function cancelOrder(orderId: string): Promise<Record<string, unknown>> {
  return request(`/paper/orders/${encodeURIComponent(orderId)}`, { method: "DELETE" });
}

export function scheduleOrder(
  order: NewOrder,
  scheduledAt: Date
): Promise<ScheduledOrderRecord> {
  return request("/paper/scheduled-orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order, scheduled_at: scheduledAt.toISOString() }),
  });
}

export function getScheduledOrders(): Promise<ScheduledOrderRecord[]> {
  return request("/paper/scheduled-orders");
}

export function cancelScheduledOrder(id: number): Promise<ScheduledOrderRecord> {
  return request(`/paper/scheduled-orders/${id}`, { method: "DELETE" });
}
