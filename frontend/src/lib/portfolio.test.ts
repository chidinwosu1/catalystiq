import { describe, expect, it } from "vitest";
import type { AccountInfo, Position } from "./api";
import {
  accountDayPnL,
  investedAmount,
  positionDayPnL,
  positionTotalPnL,
  totalUnrealizedPl,
} from "./portfolio";

function position(overrides: Partial<Position> = {}): Position {
  return {
    symbol: "VOO",
    side: "long",
    qty: "1",
    avg_entry_price: "684.65",
    market_value: "681.06",
    cost_basis: "684.65",
    unrealized_pl: "-3.59",
    unrealized_plpc: "-0.0052",
    current_price: "681.06",
    change_today: "-3.59",
    ...overrides,
  };
}

function account(overrides: Partial<AccountInfo> = {}): AccountInfo {
  return {
    status: "ACTIVE",
    currency: "USD",
    cash: "999315.35",
    buying_power: "3999304.58",
    portfolio_value: "999996.41",
    equity: "999996.41",
    last_equity: "1000000.00",
    trading_blocked: false,
    account_blocked: false,
    pattern_day_trader: false,
    ...overrides,
  };
}

describe("positionDayPnL — today's P/L (the -359% regression)", () => {
  it("reports today's DOLLAR move straight from change_today, not multiplied by 100", () => {
    // The screenshot showed VOO down $3.59 on the day.
    expect(positionDayPnL(position()).dollar).toBe(-3.59);
  });

  it("computes today's PERCENT against today's opening value (~-0.52%, never -359%)", () => {
    const { pct } = positionDayPnL(position());
    // opening value = market_value - change_today = 681.06 - (-3.59) = 684.65
    // pct = -3.59 / 684.65 * 100 = -0.5244%
    expect(pct).toBeCloseTo(-0.5244, 3);
    // Guard against the old bug that rendered change_today * 100 as a percent.
    expect(pct).not.toBeCloseTo(-359, 0);
  });

  it("matches the sandbox-verified position (change_today = -1.74)", () => {
    const p = position({ market_value: "682.91", change_today: "-1.74" });
    const { dollar, pct } = positionDayPnL(p);
    expect(dollar).toBe(-1.74);
    // opening = 682.91 - (-1.74) = 684.65 -> -1.74 / 684.65 * 100 = -0.2542%
    expect(pct).toBeCloseTo(-0.2542, 3);
  });

  it("keeps the P/L sign for a short position (denominator is absolute)", () => {
    // Short that gained: market_value -100, day P/L +5 -> opening -105.
    const p = position({ side: "short", market_value: "-100", change_today: "5" });
    const { dollar, pct } = positionDayPnL(p);
    expect(dollar).toBe(5);
    expect(pct).toBeCloseTo((5 / 105) * 100, 6);
    expect(pct).toBeGreaterThan(0);
  });

  it("returns 0% (not NaN/Infinity) when the opening value is zero", () => {
    const p = position({ market_value: "0", change_today: "0" });
    expect(positionDayPnL(p).pct).toBe(0);
  });
});

describe("positionTotalPnL — total (lifetime) P/L", () => {
  it("reports the DOLLAR move from unrealized_pl", () => {
    expect(positionTotalPnL(position()).dollar).toBe(-3.59);
  });

  it("uses the broker rate against cost basis for PERCENT (~-0.52%)", () => {
    // unrealized_plpc = -0.0052 -> -0.52%
    expect(positionTotalPnL(position()).pct).toBeCloseTo(-0.52, 6);
  });

  it("falls back to dollar / cost_basis when the broker rate is absent", () => {
    const p = position({ unrealized_plpc: "" });
    // -3.59 / 684.65 * 100 = -0.5244%
    expect(positionTotalPnL(p).pct).toBeCloseTo(-0.5244, 3);
  });

  it("returns 0% when both the rate and cost basis are unavailable", () => {
    const p = position({ unrealized_plpc: "", cost_basis: "0" });
    expect(positionTotalPnL(p).pct).toBe(0);
  });

  it("reports gains as positive dollars and percent", () => {
    const p = position({ unrealized_pl: "50.00", unrealized_plpc: "0.075" });
    const { dollar, pct } = positionTotalPnL(p);
    expect(dollar).toBe(50);
    expect(pct).toBeCloseTo(7.5, 6);
  });
});

describe("today's vs total P/L are distinct calculations", () => {
  it("uses different denominators so a same-day buy is the only case they coincide", () => {
    // Held from a prior day: today it only moved -1.00, but lifetime it is down -10.
    const p = position({
      cost_basis: "700.00",
      market_value: "690.00",
      unrealized_pl: "-10.00",
      unrealized_plpc: "-0.0142857", // -10 / 700
      change_today: "-1.00",
    });
    const day = positionDayPnL(p);
    const total = positionTotalPnL(p);
    expect(day.dollar).toBe(-1.0);
    expect(total.dollar).toBe(-10.0);
    // day% against opening (691), total% against cost basis (700) -> different.
    expect(day.pct).toBeCloseTo((-1 / 691) * 100, 4);
    expect(total.pct).toBeCloseTo(-1.42857, 4);
    expect(day.pct).not.toBeCloseTo(total.pct, 4);
  });
});

describe("accountDayPnL — account-level today's P/L", () => {
  it("computes dollar and percent from equity vs last_equity", () => {
    const { dollar, pct } = accountDayPnL(account());
    expect(dollar).toBeCloseTo(-3.59, 6);
    // -3.59 / 1_000_000 * 100 = -0.000359% -> rounds to -0.00%
    expect(pct).toBeCloseTo(-0.000359, 6);
  });

  it("returns 0% when last_equity is zero", () => {
    expect(accountDayPnL(account({ last_equity: "0" })).pct).toBe(0);
  });
});

describe("account aggregates", () => {
  it("sums unrealized P/L across positions", () => {
    const total = totalUnrealizedPl([
      position({ unrealized_pl: "-3.59" }),
      position({ symbol: "AAPL", unrealized_pl: "12.40" }),
    ]);
    expect(total).toBeCloseTo(8.81, 6);
  });

  it("computes invested as equity minus cash", () => {
    expect(investedAmount(account())).toBeCloseTo(681.06, 2);
  });
});
