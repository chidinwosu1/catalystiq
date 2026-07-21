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

export interface BrokerAccount {
  account_id: string;
  account_number: string;
  account_type: string;
  currency: string;
  status: string;
  raw: Record<string, unknown>;
}

export type OrderStatusNorm =
  | "filled"
  | "partially_filled"
  | "open"
  | "cancelled"
  | "failed"
  | "unknown";

export interface OrderRecord {
  order_id: string;
  client_order_id: string;
  symbol: string;
  side: string;
  order_type: string;
  time_in_force: string;
  status: OrderStatusNorm;
  status_raw: string;
  total_qty: string;
  filled_qty: string;
  avg_fill_price: string;
  filled_amount: string;
  commission: string;
  created_at: string;
  updated_at: string;
  raw: Record<string, unknown>;
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
  } catch (err) {
    // A caller-initiated cancellation (AbortController) is not a reachability
    // failure - rethrow it so effects can ignore superseded requests instead of
    // surfacing a misleading "could not reach the API" error.
    if (err instanceof DOMException && err.name === "AbortError") throw err;
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
  // Last successful on-demand fetch (in-process). Populated for sources served
  // live per request rather than via scheduled ingestion; null when the source
  // hasn't been fetched in this backend process.
  last_fetched_at: string | null;
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

export function getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
  return request(`/market-data/quote/${encodeURIComponent(symbol)}`, { signal });
}

export interface QuoteResult {
  symbol: string;
  status: "ok" | "unavailable";
  price: number | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  as_of: string | null;
  detail: string | null;
}

/** Batch quotes for a symbol/index list (ticker strip, market overview). A
 *  per-symbol failure comes back as status:"unavailable", never fabricated. */
export function getQuotes(symbols: string[]): Promise<QuoteResult[]> {
  return request(`/market-data/quotes?symbols=${encodeURIComponent(symbols.join(","))}`);
}

export interface SectorPerformance {
  sector: string;
  symbol: string;
  status: "ok" | "unavailable";
  daily_pct: number | null;
  weekly_pct: number | null;
  rel_strength_vs_spy: number | null;
  as_of: string | null;
}

/** Deterministic sector performance (SPDR sector ETFs, computed from real OHLCV). */
export function getSectors(): Promise<SectorPerformance[]> {
  return request("/market-data/sectors");
}

export function getOhlcv(symbol: string, days = 365): Promise<OHLCVBar[]> {
  return request(`/market-data/ohlcv/${encodeURIComponent(symbol)}?days=${days}`);
}

export function ingestPriceHistory(symbol: string, days = 365 * 5): Promise<DataQualityReport> {
  return request(`/market-data/ingest/${encodeURIComponent(symbol)}?days=${days}`, {
    method: "POST",
  });
}

export function getFundamentals(
  symbol: string,
  signal?: AbortSignal
): Promise<FundamentalsSnapshot> {
  return request(`/market-data/fundamentals/${encodeURIComponent(symbol)}`, { signal });
}

export function getTechnicalSnapshot(symbol: string, days = 365 * 5): Promise<TechnicalSnapshot> {
  return request(`/analysis/technical/${encodeURIComponent(symbol)}?days=${days}`);
}

// --- Rule-Based Opportunity Score (Setup Strength) ---------------------
// A transparent, deterministic technical setup-strength score. NOT a
// probability of profit, AI confidence, or ML prediction. The `ml` block is
// always present and not_available until validated models exist.

export interface OpportunityFactor {
  name: string;
  score: number | null;
  max_score: number;
  status: "available" | "insufficient_data";
  inputs: Record<string, unknown>;
  explanation: string;
  formula_version: string;
}

export interface OpportunityUnavailableFactor {
  name: string;
  reason: string;
}

export interface EntryQualityComponent {
  name: string;
  score: number | null;
  max_score: number;
  status: "available" | "insufficient_data";
  inputs: Record<string, unknown>;
  explanation: string;
  formula_version: string;
}

/**
 * The real-time, intraday Entry Quality Score - INDEPENDENT of the daily Setup
 * Strength (OpportunityScore). Answers "is this a high-quality MOMENT to
 * enter?" vs Setup Strength's "is this a high-quality STOCK to trade?".
 */
export interface EntryQualityScore {
  symbol: string;
  status: "available" | "insufficient_data";
  score_type: string; // "entry_quality"
  score: number | null;
  max_score: number;
  rating: string | null; // "Excellent Entry" .. "Poor Entry"
  formula_version: string;
  calculated_at: string;
  data_as_of: string | null;
  interval: string | null;
  component_coverage: string;
  components: EntryQualityComponent[];
  warnings: string[];
  reason: string | null;
}

export interface OpportunityScore {
  symbol: string;
  status: "available" | "insufficient_data";
  score_type: string; // "rule_based"
  score: number | null;
  max_score: number;
  label: string | null;
  formula_version: string;
  calculated_at: string;
  data_as_of: string | null;
  freshness: string;
  factor_coverage: string;
  factors: OpportunityFactor[];
  unavailable_factors: OpportunityUnavailableFactor[];
  warnings: string[];
  ml: { status: string; reason: string };
  reason: string | null;
  /** Independent real-time Entry Quality; null when not computed. */
  entry_quality: EntryQualityScore | null;
}

export function getEntryQualityScore(symbol: string): Promise<EntryQualityScore> {
  return request(`/analysis/${encodeURIComponent(symbol)}/entry-quality`);
}

export function getOpportunityScore(symbol: string): Promise<OpportunityScore> {
  return request(`/analysis/${encodeURIComponent(symbol)}/opportunity-score`);
}

export interface OpportunityScan {
  as_of: string;
  formula_version: string;
  universe_size: number;
  eligible_count: number;
  top: number;
  candidates: OpportunityScore[];
  ml: { status: string; reason: string };
  note: string | null;
}

/** Ranked rule-based candidates from a curated universe scan (top N). */
export function getOpportunityScan(top = 4): Promise<OpportunityScan> {
  return request(`/analysis/opportunity-scan?top=${top}`);
}

// A completed/in-flight scan is shared across components so that, e.g., the
// Market Intelligence and Trade Center pages mounting together reuse ONE scan
// instead of each launching a full universe scan (which also protects against
// React StrictMode's double-invoked effects in dev). The window is short so the
// data stays fresh; a failed scan is not cached, so a later mount can retry.
const SCAN_SHARE_MS = 30_000;
let _scanShared: { top: number; at: number; promise: Promise<OpportunityScan> } | null = null;

export function getOpportunityScanShared(top = 4): Promise<OpportunityScan> {
  const now = Date.now();
  if (_scanShared && _scanShared.top === top && now - _scanShared.at < SCAN_SHARE_MS) {
    return _scanShared.promise;
  }
  const promise = getOpportunityScan(top);
  const entry = { top, at: now, promise };
  _scanShared = entry;
  promise.catch(() => {
    // Drop a failed scan so it isn't served from the share cache.
    if (_scanShared === entry) _scanShared = null;
  });
  return promise;
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

/** Read-only broker account list (confirms the API account id). */
export function getBrokerAccounts(): Promise<BrokerAccount[]> {
  return request("/paper/accounts");
}

/**
 * Read-only, normalized order history. Webull defaults to the last 7 days
 * when no date range is given. Pass `filledOnly` for the Portfolio filled-
 * orders view. Never places or cancels an order.
 */
export function getOrderHistory(opts?: {
  symbol?: string;
  filledOnly?: boolean;
  startDate?: string;
  endDate?: string;
}): Promise<OrderRecord[]> {
  const params = new URLSearchParams();
  if (opts?.symbol) params.set("symbol", opts.symbol);
  if (opts?.filledOnly) params.set("filled_only", "true");
  if (opts?.startDate) params.set("start_date", opts.startDate);
  if (opts?.endDate) params.set("end_date", opts.endDate);
  const qs = params.toString();
  return request(`/paper/order-history${qs ? `?${qs}` : ""}`);
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
