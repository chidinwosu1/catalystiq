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
