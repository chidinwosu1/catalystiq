/**
 * Typed hooks for the genuinely-live values in Catalyst IQ, all backed by the
 * shared `liveCache` (15s polling, visibility-aware, deduped). Because the keys
 * are stable strings, any two components asking for the same thing — the
 * account balance on the Portfolio page and on the Trade Ticket, say — reuse a
 * single request and a single polling loop.
 *
 * Read-only: these only issue GET reads (account, positions, quotes). Nothing
 * here places, modifies, or cancels an order.
 */
import {
  getAccount,
  getPositions,
  getQuote,
  getQuotes,
  type AccountInfo,
  type Position,
  type Quote,
  type QuoteResult,
} from "./api";
import { useLiveQuery, type LiveQueryResult } from "./useLiveQuery";

/** Live account balance (equity, cash, buying power). */
export function useLiveAccount(): LiveQueryResult<AccountInfo> {
  return useLiveQuery<AccountInfo>("account", getAccount);
}

/** Live open positions (market value, unrealized P/L, today's move). */
export function useLivePositions(): LiveQueryResult<Position[]> {
  return useLiveQuery<Position[]>("positions", getPositions);
}

function symbolsKey(symbols: string[]): { key: string; list: string[] } {
  const list = Array.from(new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))).sort();
  return { key: list.length ? `quotes:${list.join(",")}` : "quotes:none", list };
}

/**
 * Live batch quotes for a set of symbols (ticker strips, market overview,
 * candidate price columns). The key is normalized so the same set of symbols in
 * any order shares one request across pages. Inert for an empty set.
 */
export function useLiveQuotes(symbols: string[]): LiveQueryResult<QuoteResult[]> {
  const { key, list } = symbolsKey(symbols);
  return useLiveQuery<QuoteResult[]>(key, () => getQuotes(list), { enabled: list.length > 0 });
}

/** Live single-symbol quote (trade ticket, analysis header). Inert when blank. */
export function useLiveQuote(symbol: string): LiveQueryResult<Quote> {
  const sym = symbol.trim().toUpperCase();
  return useLiveQuery<Quote>(sym ? `quote:${sym}` : "quote:none", () => getQuote(sym), {
    enabled: sym.length > 0,
  });
}
