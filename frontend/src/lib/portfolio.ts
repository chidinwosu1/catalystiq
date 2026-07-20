/**
 * Pure profit/loss math for the Portfolio page.
 *
 * These helpers keep two things that are easy to confuse strictly separate:
 *
 *   - TODAY'S P/L  — the intraday move, measured against today's OPENING value.
 *   - TOTAL P/L    — the lifetime move, measured against COST BASIS.
 *
 * They must not be multiplied together or share a denominator. The broker
 * (Webull, mapped in catalystiq/providers/broker.py) reports:
 *   - `unrealized_pl`   : total P/L in DOLLARS      (unrealized_profit_loss)
 *   - `unrealized_plpc` : total P/L as a RATE       (unrealized_profit_loss_rate, e.g. -0.0052)
 *   - `change_today`    : today's P/L in DOLLARS    (day_profit_loss)  <-- NOT a rate
 *
 * The previous UI treated `change_today` as a rate and multiplied it by 100,
 * turning a -$3.59 day into "-359.00%". These functions fix that.
 */
import type { AccountInfo, Position } from "./api";

export interface PnL {
  /** Absolute profit/loss in the account currency. */
  dollar: number;
  /** Profit/loss as a percentage value, e.g. -0.52 means -0.52%. */
  pct: number;
}

/** Parse a broker string/number field into a finite number (0 when absent/garbage). */
function num(value: string | number | null | undefined): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

function hasValue(value: string | number | null | undefined): boolean {
  return value !== "" && value !== null && value !== undefined;
}

/**
 * Total (lifetime) P/L for a single position.
 *
 * Dollar is the broker's unrealized P/L. The percentage is measured against
 * COST BASIS: the broker supplies it as a decimal rate (`unrealized_plpc`),
 * which we render as a percent. When that rate is missing we fall back to
 * dollar / cost_basis.
 */
export function positionTotalPnL(p: Position): PnL {
  const dollar = num(p.unrealized_pl);
  const costBasis = num(p.cost_basis);
  const pct = hasValue(p.unrealized_plpc)
    ? num(p.unrealized_plpc) * 100
    : costBasis !== 0
      ? (dollar / costBasis) * 100
      : 0;
  return { dollar, pct };
}

/**
 * Today's P/L for a single position.
 *
 * `change_today` is the broker's day P/L as a DOLLAR amount, not a rate. The
 * percentage is measured against TODAY'S OPENING value — the market value
 * before today's move, i.e. `market_value - change_today` — which is the
 * correct denominator for an intraday return. `Math.abs` on the denominator
 * keeps the sign coming from the P/L itself, which is also correct for shorts.
 */
export function positionDayPnL(p: Position): PnL {
  const dollar = num(p.change_today);
  const marketValue = num(p.market_value);
  const openValue = marketValue - dollar;
  const pct = openValue !== 0 ? (dollar / Math.abs(openValue)) * 100 : 0;
  return { dollar, pct };
}

/**
 * Account-level "today" P/L: current equity minus yesterday's closing equity,
 * measured against that prior equity.
 */
export function accountDayPnL(account: AccountInfo): PnL {
  const equity = num(account.equity);
  const lastEquity = num(account.last_equity);
  const dollar = equity - lastEquity;
  const pct = lastEquity !== 0 ? (dollar / lastEquity) * 100 : 0;
  return { dollar, pct };
}

/** Sum of unrealized (total, lifetime) P/L across positions, in dollars. */
export function totalUnrealizedPl(positions: Position[]): number {
  return positions.reduce((sum, p) => sum + num(p.unrealized_pl), 0);
}

/** Amount currently deployed in positions: equity minus cash. */
export function investedAmount(account: AccountInfo): number {
  return num(account.equity) - num(account.cash);
}
