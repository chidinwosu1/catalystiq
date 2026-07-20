/**
 * A tiny shared query/cache layer for GENUINELY LIVE data (quotes, account
 * balance, positions) — the values that should refresh every 15 seconds.
 *
 * It exists so that multiple components and pages that need the same live
 * value reuse ONE polling loop and ONE in-flight request instead of each
 * calling the provider independently. Slow-changing data (indicators, scores,
 * ML, fundamentals, macro, news, completed analysis) must NOT go through here —
 * it keeps its own freshness schedule.
 *
 * Guarantees (see liveCache.test.ts):
 *   - polls a key only while it has ≥1 subscriber AND the tab is visible;
 *   - stops polling when the last subscriber unmounts or the tab is hidden;
 *   - refreshes immediately on focus / reconnect / becoming visible again;
 *   - deduplicates simultaneous requests for the same key (one in-flight promise);
 *   - preserves the last successful value on failure (never wipes data);
 *   - exposes lastUpdated + a stale flag (last refresh errored);
 *   - backs off exponentially (capped) after a 429 and resets on success.
 *
 * This module is framework-agnostic and its clock/visibility are injectable so
 * it can be tested in Node with fake timers. The React binding is `useLiveQuery`.
 */

export const LIVE_REFRESH_MS = 15_000;
const MAX_BACKOFF_STEPS = 3; // 15s → 30s → 60s → 120s cap
// Coalesce the burst of "refresh now" triggers that fire together when a tab
// regains focus (visibilitychange + focus + online can all arrive at once).
const FOCUS_THROTTLE_MS = 1_000;

export type QueryStatus = "idle" | "loading" | "success" | "error";

export interface QuerySnapshot<T> {
  /** Last successful value; undefined until the first success. */
  data: T | undefined;
  /** Last error; cleared on the next success. Present alongside `data` means stale. */
  error: Error | undefined;
  /** Epoch ms of the last successful fetch. */
  lastUpdated: number | undefined;
  status: QueryStatus;
  /** A request is currently in flight. */
  isFetching: boolean;
}

const IDLE_SNAPSHOT: QuerySnapshot<unknown> = {
  data: undefined,
  error: undefined,
  lastUpdated: undefined,
  status: "idle",
  isFetching: false,
};

interface Entry {
  key: string;
  fetcher: () => Promise<unknown>;
  data: unknown;
  error: Error | undefined;
  lastUpdated: number | undefined;
  status: QueryStatus;
  isFetching: boolean;
  promise: Promise<unknown> | undefined;
  subscribers: Set<() => void>;
  timer: ReturnType<typeof setTimeout> | undefined;
  backoff: number;
  intervalMs: number;
  snapshot: QuerySnapshot<unknown>;
}

export interface LiveCacheOptions {
  /** Whether polling is allowed right now (tab visible). Injectable for tests. */
  isVisible?: () => boolean;
  /** Clock, injectable for tests. */
  now?: () => number;
}

function status429(err: unknown): boolean {
  return typeof err === "object" && err !== null && (err as { status?: number }).status === 429;
}

export class LiveCache {
  private entries = new Map<string, Entry>();
  private isVisible: () => boolean;
  private now: () => number;

  constructor(opts: LiveCacheOptions = {}) {
    this.isVisible =
      opts.isVisible ??
      (() => typeof document === "undefined" || document.visibilityState === "visible");
    this.now = opts.now ?? (() => Date.now());
  }

  /**
   * Register interest in `key`. The first subscriber triggers an initial fetch
   * and starts polling; the returned function unsubscribes (and stops polling
   * once the last subscriber leaves). `onStoreChange` is called on every state
   * change so React's useSyncExternalStore can re-read the snapshot.
   */
  subscribe(
    key: string,
    fetcher: () => Promise<unknown>,
    onStoreChange: () => void,
    intervalMs: number = LIVE_REFRESH_MS
  ): () => void {
    let entry = this.entries.get(key);
    if (!entry) {
      entry = {
        key,
        fetcher,
        data: undefined,
        error: undefined,
        lastUpdated: undefined,
        status: "idle",
        isFetching: false,
        promise: undefined,
        subscribers: new Set(),
        timer: undefined,
        backoff: 0,
        intervalMs,
        snapshot: IDLE_SNAPSHOT,
      };
      this.entries.set(key, entry);
    } else {
      entry.fetcher = fetcher; // adopt the latest closure (same key ⇒ same request)
    }
    entry.subscribers.add(onStoreChange);

    if (entry.data === undefined && !entry.isFetching) {
      // Nothing cached yet → initial load (which then schedules the next poll).
      void this.fetch(entry);
    } else {
      // Cached data already present (e.g. a remount) → just (re)start polling.
      this.schedule(entry);
    }

    return () => this.unsubscribe(key, onStoreChange);
  }

  private unsubscribe(key: string, onStoreChange: () => void): void {
    const entry = this.entries.get(key);
    if (!entry) return;
    entry.subscribers.delete(onStoreChange);
    if (entry.subscribers.size === 0) {
      // No consumers left: stop polling. The entry (and its last value) is kept
      // so a quick remount reuses it instantly instead of flashing "loading".
      this.clearTimer(entry);
    }
  }

  getSnapshot<T>(key: string): QuerySnapshot<T> {
    const entry = this.entries.get(key);
    return (entry ? entry.snapshot : IDLE_SNAPSHOT) as QuerySnapshot<T>;
  }

  /** Force a refresh now (manual button / focus). Deduped against any in-flight request. */
  refetch(key: string): Promise<unknown> {
    const entry = this.entries.get(key);
    if (!entry) return Promise.resolve(undefined);
    return this.fetch(entry, true);
  }

  private fetch(entry: Entry, manual = false): Promise<unknown> {
    // Dedup: a request is already in flight → everyone shares it.
    if (entry.promise) return entry.promise;
    // Never auto-poll a hidden tab; a manual/explicit refresh still goes through.
    if (!manual && !this.isVisible()) return Promise.resolve(entry.data);

    entry.isFetching = true;
    if (entry.data === undefined) entry.status = "loading";
    this.commit(entry);

    const p = entry
      .fetcher()
      .then((data) => {
        entry.data = data;
        entry.error = undefined;
        entry.lastUpdated = this.now();
        entry.status = "success";
        entry.backoff = 0; // recovered → resume the fast cadence
        return data;
      })
      .catch((err: unknown) => {
        // Preserve the last good value; surface the error and mark it stale.
        entry.error = err instanceof Error ? err : new Error(String(err));
        entry.status = entry.data !== undefined ? "success" : "error";
        if (status429(err)) {
          entry.backoff = Math.min(entry.backoff + 1, MAX_BACKOFF_STEPS);
        }
        return entry.data;
      })
      .finally(() => {
        entry.promise = undefined;
        entry.isFetching = false;
        this.commit(entry);
        this.schedule(entry);
      });

    entry.promise = p;
    return p;
  }

  private schedule(entry: Entry): void {
    this.clearTimer(entry);
    if (entry.subscribers.size === 0) return; // no consumers → no polling
    if (!this.isVisible()) return; // hidden → paused (resumes on visibility)
    const delay = entry.intervalMs * 2 ** Math.min(entry.backoff, MAX_BACKOFF_STEPS);
    entry.timer = setTimeout(() => void this.fetch(entry), delay);
  }

  private clearTimer(entry: Entry): void {
    if (entry.timer !== undefined) {
      clearTimeout(entry.timer);
      entry.timer = undefined;
    }
  }

  private commit(entry: Entry): void {
    entry.snapshot = {
      data: entry.data,
      error: entry.error,
      lastUpdated: entry.lastUpdated,
      status: entry.status,
      isFetching: entry.isFetching,
    };
    entry.subscribers.forEach((cb) => cb());
  }

  /** Called by the DOM layer / tests when the tab's visibility flips. */
  handleVisibilityChange(): void {
    if (this.isVisible()) {
      this.refreshActive(FOCUS_THROTTLE_MS); // catch up, then polling resumes
    } else {
      for (const entry of this.entries.values()) this.clearTimer(entry); // pause
    }
  }

  /** Called on window focus / reconnect (online): refresh active queries now. */
  handleFocusOrReconnect(): void {
    if (!this.isVisible()) return;
    this.refreshActive(FOCUS_THROTTLE_MS);
  }

  private refreshActive(throttleMs: number): void {
    const now = this.now();
    for (const entry of this.entries.values()) {
      if (entry.subscribers.size === 0) continue;
      // Skip a query refreshed a moment ago so a focus burst doesn't stampede.
      if (entry.lastUpdated !== undefined && now - entry.lastUpdated < throttleMs) {
        this.schedule(entry);
        continue;
      }
      void this.fetch(entry);
    }
  }

  /** Test/debug helper: number of live subscribers for a key. */
  subscriberCount(key: string): number {
    return this.entries.get(key)?.subscribers.size ?? 0;
  }

  /** Test helper: whether a poll timer is currently armed for a key. */
  isPolling(key: string): boolean {
    return this.entries.get(key)?.timer !== undefined;
  }
}

/** The app-wide singleton every page/component shares. */
export const liveCache = new LiveCache();

let domListenersInstalled = false;

/**
 * Wire the singleton to real browser events once. Idempotent and safe to call
 * from every hook mount. No-op outside a browser (SSR / Node tests).
 */
export function installLiveCacheDomListeners(): void {
  if (domListenersInstalled || typeof document === "undefined") return;
  domListenersInstalled = true;
  document.addEventListener("visibilitychange", () => liveCache.handleVisibilityChange());
  window.addEventListener("focus", () => liveCache.handleFocusOrReconnect());
  window.addEventListener("online", () => liveCache.handleFocusOrReconnect());
}
