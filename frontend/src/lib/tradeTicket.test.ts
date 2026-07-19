import { describe, expect, it } from "vitest";
import type { Quote } from "./api";
import {
  canReviewOrder,
  estimatedValue,
  isQuoteUsable,
  referencePrice,
} from "./tradeTicket";

const NOW = Date.parse("2024-01-02T15:00:00Z");

function quote(price: number, asOf: string = "2024-01-02T14:59:00Z"): Quote {
  return { symbol: "NVDA", price, previous_close: price - 1, as_of: asOf };
}

describe("referencePrice / estimatedValue", () => {
  it("updates the estimate as quantity changes when a price is present", () => {
    const rp = referencePrice({ orderType: "market", limitPrice: "", quote: quote(100), nowMs: NOW });
    expect(rp).toBe(100);
    // The bug: estimate was frozen at $0.00 regardless of qty. Now it tracks qty.
    expect(estimatedValue(10, rp)).toBe(1000);
    expect(estimatedValue(25, rp)).toBe(2500);
    expect(estimatedValue(3, rp)).toBe(300);
  });

  it("returns null (never 0) when the quote is missing, so the estimate is 'unavailable' not $0.00", () => {
    const rp = referencePrice({ orderType: "market", limitPrice: "", quote: null, nowMs: NOW });
    expect(rp).toBeNull();
    expect(estimatedValue(10, rp)).toBeNull();
    expect(estimatedValue(999, rp)).toBeNull(); // any qty -> still unavailable, not $0
  });

  it("treats a zero/negative price as unavailable rather than a real $0 price", () => {
    expect(referencePrice({ orderType: "market", limitPrice: "", quote: quote(0), nowMs: NOW })).toBeNull();
  });

  it("uses the user's limit price as the reference for limit orders", () => {
    const rp = referencePrice({ orderType: "limit", limitPrice: "42.5", quote: null, nowMs: NOW });
    expect(rp).toBe(42.5);
    expect(estimatedValue(4, rp)).toBe(170);
  });
});

describe("quote freshness (retain only if fresh)", () => {
  it("accepts a recent quote and rejects a stale one", () => {
    expect(isQuoteUsable(quote(100, "2024-01-02T14:59:00Z"), NOW)).toBe(true); // 1 min old
    expect(isQuoteUsable(quote(100, "2024-01-02T14:30:00Z"), NOW)).toBe(false); // 30 min old
  });

  it("a stale quote yields no reference price (never fabricated forward)", () => {
    const stale = quote(100, "2024-01-02T14:00:00Z"); // 1 hour old
    expect(referencePrice({ orderType: "market", limitPrice: "", quote: stale, nowMs: NOW })).toBeNull();
  });
});

describe("canReviewOrder blocks when price is unavailable", () => {
  const base = {
    symbol: "NVDA",
    qtyNum: 10,
    assetType: "stocks",
    orderType: "market" as const,
    limitPrice: "",
    stopPrice: "",
    trailPercent: "2",
    executionMode: "now",
    scheduledValid: false,
  };

  it("allows review with a valid price", () => {
    expect(canReviewOrder({ ...base, refPrice: 100 })).toBe(true);
  });

  it("blocks review when the required price is unavailable", () => {
    expect(canReviewOrder({ ...base, refPrice: null })).toBe(false);
  });

  it("blocks review with zero quantity even if priced", () => {
    expect(canReviewOrder({ ...base, qtyNum: 0, refPrice: 100 })).toBe(false);
  });
});
