import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpportunityScan, ScanPreferencesInput } from "./api";

/** A minimal OpportunityScan body for mocked responses. */
function fakeScan(): OpportunityScan {
  return {
    as_of: "2026-07-18T00:00:00Z",
    formula_version: "opportunity_score_v1",
    universe_size: 24,
    eligible_count: 0,
    top: 4,
    candidates: [],
    ml: { status: "not_available", reason: "" },
    note: null,
    status: "ok",
  };
}

const PROFILE_A: ScanPreferencesInput = {
  style: "long",
  risk: "conservative",
  amount: 10000,
  maxLossPct: 5,
  direction: "long",
  assets: ["Stocks"],
};

const PROFILE_B: ScanPreferencesInput = {
  style: "intraday",
  risk: "aggressive",
  amount: 2000,
  maxLossPct: 1,
  direction: "both",
  assets: ["Stocks"],
};

describe("opportunity-scan preferences", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  // Fresh module (fresh share-cache state) per test so counts are isolated.
  let api: typeof import("./api");

  beforeEach(async () => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => fakeScan() }));
    vi.stubGlobal("fetch", fetchMock);
    vi.resetModules();
    api = await import("./api");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function lastUrl(): string {
    return String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]);
  }

  it("serializes every preference into the request query", async () => {
    await api.getOpportunityScan(4, PROFILE_A);
    const url = lastUrl();
    expect(url).toContain("top=4");
    expect(url).toContain("style=long");
    expect(url).toContain("risk=conservative");
    expect(url).toContain("amount=10000");
    expect(url).toContain("max_loss_pct=5");
    expect(url).toContain("direction=long");
    expect(url).toContain("assets=Stocks");
    expect(url).toContain("fractional_shares=true");
  });

  it("omits preference params entirely for a generic scan", async () => {
    await api.getOpportunityScan(4);
    const url = lastUrl();
    expect(url).toContain("top=4");
    expect(url).not.toContain("style=");
    expect(url).not.toContain("risk=");
  });

  it("shares a result only for the SAME preferences within the window", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    await api.getOpportunityScanShared(4, PROFILE_A);
    await api.getOpportunityScanShared(4, PROFILE_A); // same profile -> cache hit
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("bypasses the share cache when preferences change (invalidation)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    await api.getOpportunityScanShared(4, PROFILE_A);
    // A different profile must NOT be served profile A's cached result: submitting
    // new preferences forces a fresh request for the new preference set.
    await api.getOpportunityScanShared(4, PROFILE_B);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls[0]).toContain("risk=conservative");
    expect(urls[1]).toContain("risk=aggressive");
    expect(urls[1]).toContain("direction=both");
  });
});
