import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveCache } from "./liveCache";

/** A promise whose resolution we control, for exercising in-flight dedup. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("LiveCache", () => {
  let visible = true;
  let clock = 0;
  let cache: LiveCache;

  beforeEach(() => {
    vi.useFakeTimers();
    visible = true;
    clock = 0;
    cache = new LiveCache({ isVisible: () => visible, now: () => clock });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Advance both the timer clock and the injected wall clock together, flushing
  // the microtasks that resolve each fetch.
  async function advance(ms: number) {
    clock += ms;
    await vi.advanceTimersByTimeAsync(ms);
  }

  describe("request deduplication", () => {
    it("issues ONE request when several subscribers ask for the same key at once", () => {
      const d = deferred<number[]>();
      const fetcher = vi.fn(() => d.promise);
      cache.subscribe("k", fetcher, vi.fn());
      cache.subscribe("k", fetcher, vi.fn());
      cache.subscribe("k", fetcher, vi.fn());
      expect(fetcher).toHaveBeenCalledTimes(1);
    });

    it("coalesces a manual refetch into the request already in flight", async () => {
      const d = deferred<number[]>();
      const fetcher = vi.fn(() => d.promise);
      const cb1 = vi.fn();
      const cb2 = vi.fn();
      cache.subscribe("k", fetcher, cb1);
      cache.subscribe("k", fetcher, cb2);

      cache.refetch("k"); // while the first request is still pending
      cache.refetch("k");
      expect(fetcher).toHaveBeenCalledTimes(1);

      d.resolve([1, 2, 3]);
      await advance(0);

      expect(cache.getSnapshot<number[]>("k").data).toEqual([1, 2, 3]);
      expect(cb1).toHaveBeenCalled();
      expect(cb2).toHaveBeenCalled();
    });

    it("keeps separate keys independent", () => {
      const a = vi.fn(() => new Promise(() => {}));
      const b = vi.fn(() => new Promise(() => {}));
      cache.subscribe("a", a, vi.fn());
      cache.subscribe("b", b, vi.fn());
      expect(a).toHaveBeenCalledTimes(1);
      expect(b).toHaveBeenCalledTimes(1);
    });
  });

  describe("polling lifecycle", () => {
    it("polls every 15s while visible with a subscriber", async () => {
      const fetcher = vi.fn().mockResolvedValue("v");
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(1); // initial
      await advance(15_000);
      expect(fetcher).toHaveBeenCalledTimes(2);
      await advance(15_000);
      expect(fetcher).toHaveBeenCalledTimes(3);
    });

    it("STOPS polling once the last subscriber unmounts", async () => {
      const fetcher = vi.fn().mockResolvedValue("v");
      const unsubscribe = cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(1);

      unsubscribe();
      expect(cache.subscriberCount("k")).toBe(0);
      expect(cache.isPolling("k")).toBe(false);

      await advance(60_000);
      expect(fetcher).toHaveBeenCalledTimes(1); // no further polling
    });

    it("keeps polling while at least one of several subscribers remains", async () => {
      const fetcher = vi.fn().mockResolvedValue("v");
      const unsubA = cache.subscribe("k", fetcher, vi.fn());
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      unsubA(); // one leaves, one stays
      expect(cache.isPolling("k")).toBe(true);
      await advance(15_000);
      expect(fetcher).toHaveBeenCalledTimes(2);
    });
  });

  describe("visibility", () => {
    it("STOPS polling when the tab is hidden and resumes on return", async () => {
      const fetcher = vi.fn().mockResolvedValue("v");
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      await advance(15_000);
      expect(fetcher).toHaveBeenCalledTimes(2);

      // Hide the tab.
      visible = false;
      cache.handleVisibilityChange();
      expect(cache.isPolling("k")).toBe(false);
      await advance(60_000);
      expect(fetcher).toHaveBeenCalledTimes(2); // frozen while hidden

      // Return to the tab → immediate catch-up refresh, then polling resumes.
      visible = true;
      cache.handleVisibilityChange();
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(3);
      await advance(15_000);
      expect(fetcher).toHaveBeenCalledTimes(4);
    });

    it("does NOT fetch when a subscriber mounts while the tab is hidden", async () => {
      visible = false;
      const fetcher = vi.fn().mockResolvedValue("v");
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(0);
      expect(cache.getSnapshot("k").status).toBe("idle");

      visible = true;
      cache.handleVisibilityChange();
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(1);
    });

    it("refreshes on focus/reconnect, but throttles a focus burst", async () => {
      const fetcher = vi.fn().mockResolvedValue("v");
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(1); // lastUpdated = clock 0

      // Focus + online firing together within the throttle window → one refresh.
      await advance(5_000); // clock 5s, > 1s throttle
      cache.handleFocusOrReconnect();
      cache.handleFocusOrReconnect();
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(2);
    });
  });

  describe("failure handling", () => {
    it("preserves the last successful value and flags the error as stale", async () => {
      const err = new Error("boom");
      const fetcher = vi.fn().mockResolvedValueOnce("A").mockRejectedValueOnce(err);
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      expect(cache.getSnapshot("k").data).toBe("A");

      await advance(15_000); // second poll rejects
      const snap = cache.getSnapshot("k");
      expect(snap.data).toBe("A"); // last good value kept
      expect(snap.error).toBe(err);
      expect(snap.status).toBe("success"); // still "have data", just stale
    });

    it("reports error status when the very first fetch fails (no data yet)", async () => {
      const fetcher = vi.fn().mockRejectedValue(new Error("down"));
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      const snap = cache.getSnapshot("k");
      expect(snap.data).toBeUndefined();
      expect(snap.status).toBe("error");
    });

    it("backs off after a 429 (30s instead of 15s) and resets on success", async () => {
      const err = Object.assign(new Error("rate limited"), { status: 429 });
      const fetcher = vi
        .fn()
        .mockResolvedValueOnce("A")
        .mockRejectedValueOnce(err) // triggers backoff
        .mockResolvedValue("B");
      cache.subscribe("k", fetcher, vi.fn());
      await advance(0);
      expect(fetcher).toHaveBeenCalledTimes(1);

      await advance(15_000); // poll #2 → 429 → next delay doubles to 30s
      expect(fetcher).toHaveBeenCalledTimes(2);

      await advance(15_000); // only 15s of the 30s backoff elapsed → no poll
      expect(fetcher).toHaveBeenCalledTimes(2);

      await advance(15_000); // 30s reached → poll #3 succeeds, backoff resets
      expect(fetcher).toHaveBeenCalledTimes(3);

      await advance(15_000); // back to the 15s cadence
      expect(fetcher).toHaveBeenCalledTimes(4);
    });
  });
});
