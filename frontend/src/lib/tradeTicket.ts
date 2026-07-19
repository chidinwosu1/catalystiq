/**
 * Pure, framework-free helpers for the Trade Ticket's price / estimate logic.
 *
 * These are extracted from the component so the rules can be unit-tested and so
 * the "missing price" behavior is explicit and centralized:
 *
 *  - A missing/zero/stale quote yields a reference price of `null` (NOT 0), so
 *    the estimated order value is "cannot be calculated" rather than a
 *    misleading $0.00 that never responds to the quantity.
 *  - A previously-validated quote is only reusable while it is still fresh per
 *    the freshness window (`QUOTE_MAX_AGE_MS`); a stale quote is treated as
 *    unavailable, never fabricated forward.
 *  - Review/submission is blocked whenever a required price is unavailable.
 */
import type { OrderType, Quote } from "./api";

/**
 * How long a validated quote may be reused after its `as_of` before it is
 * considered stale. Intraday prices move, so this is deliberately short; a
 * quote older than this is treated as unavailable (price cannot be trusted for
 * an order estimate) rather than shown as current.
 */
export const QUOTE_MAX_AGE_MS = 15 * 60 * 1000; // 15 minutes

/** Whether a quote is present, has a real positive price, and is still fresh. */
export function isQuoteUsable(
  quote: Quote | null,
  nowMs: number,
  maxAgeMs: number = QUOTE_MAX_AGE_MS
): boolean {
  if (!quote) return false;
  if (quote.price == null || !Number.isFinite(quote.price) || quote.price <= 0) return false;
  const asOf = Date.parse(quote.as_of);
  if (Number.isNaN(asOf)) return false;
  return nowMs - asOf <= maxAgeMs;
}

/**
 * The reference price used for the order-value estimate, or `null` when no
 * usable price is available. For limit / stop-limit orders the user's own limit
 * price is the reference when set; otherwise a usable live quote is required.
 * Never returns 0 as a stand-in for "unknown".
 */
export function referencePrice(opts: {
  orderType: OrderType;
  limitPrice: string;
  quote: Quote | null;
  nowMs: number;
  maxAgeMs?: number;
}): number | null {
  const { orderType, limitPrice, quote, nowMs, maxAgeMs } = opts;
  if (orderType === "limit" || orderType === "stop_limit") {
    const lp = parseFloat(limitPrice);
    if (lp > 0) return lp;
  }
  return isQuoteUsable(quote, nowMs, maxAgeMs) ? (quote as Quote).price : null;
}

/**
 * Estimated order value, or `null` when it cannot be computed (no usable price
 * or non-positive quantity). Callers render `null` as "estimate cannot be
 * calculated", never as $0.00.
 */
export function estimatedValue(qtyNum: number, refPrice: number | null): number | null {
  if (refPrice == null) return null;
  if (!(qtyNum > 0)) return null;
  return qtyNum * refPrice;
}

/** Whether the order can be reviewed/submitted. A usable price is required. */
export function canReviewOrder(opts: {
  symbol: string;
  qtyNum: number;
  assetType: string;
  orderType: OrderType;
  limitPrice: string;
  stopPrice: string;
  trailPercent: string;
  executionMode: string;
  scheduledValid: boolean;
  refPrice: number | null;
}): boolean {
  const {
    symbol,
    qtyNum,
    assetType,
    orderType,
    limitPrice,
    stopPrice,
    trailPercent,
    executionMode,
    scheduledValid,
    refPrice,
  } = opts;
  if (symbol.length === 0) return false;
  if (!(qtyNum > 0)) return false;
  if (assetType !== "stocks") return false;
  // A required price must be available - no reviewing an order against an
  // unknown/stale price.
  if (refPrice == null) return false;
  if (orderType === "limit" && !(parseFloat(limitPrice) > 0)) return false;
  if (orderType === "stop" && !(parseFloat(stopPrice) > 0)) return false;
  if (orderType === "stop_limit" && !(parseFloat(limitPrice) > 0 && parseFloat(stopPrice) > 0))
    return false;
  if (orderType === "trailing_stop" && !(parseFloat(trailPercent) > 0)) return false;
  if (executionMode === "scheduled" && !scheduledValid) return false;
  return true;
}

/** Human-readable "as of" label for a quote timestamp. */
export function formatQuoteAsOf(asOf: string | null | undefined): string | null {
  if (!asOf) return null;
  const ms = Date.parse(asOf);
  if (Number.isNaN(ms)) return null;
  return new Date(ms).toLocaleString();
}
