import { useCallback, useRef, useSyncExternalStore } from "react";
import {
  installLiveCacheDomListeners,
  liveCache,
  LIVE_REFRESH_MS,
  type QuerySnapshot,
} from "./liveCache";

export interface LiveQueryResult<T> extends QuerySnapshot<T> {
  /** True when the last refresh failed but a previous value is still shown. */
  isStale: boolean;
  /** Force an immediate, deduped refresh (e.g. a manual "Refresh" button). */
  refetch: () => void;
}

const IDLE: QuerySnapshot<unknown> = {
  data: undefined,
  error: undefined,
  lastUpdated: undefined,
  status: "idle",
  isFetching: false,
};

// Attach the visibility/focus/online listeners once when this module loads in a
// browser (no-op under SSR / Node tests). Idempotent.
installLiveCacheDomListeners();

export interface LiveQueryOptions {
  /** When false the query is inert: no subscription, no fetch, idle snapshot. */
  enabled?: boolean;
  /** Poll interval; defaults to the shared 15s live cadence. */
  intervalMs?: number;
}

/**
 * Subscribe a component to a live value in the shared cache. Every component
 * that passes the same `key` shares one polling loop and one in-flight request.
 *
 * `fetcher` may change identity between renders (it usually closes over props);
 * the latest one is always used, and only `key`/`enabled` re-subscribe.
 */
export function useLiveQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: LiveQueryOptions = {}
): LiveQueryResult<T> {
  const { enabled = true, intervalMs = LIVE_REFRESH_MS } = options;

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (!enabled) return () => {};
      return liveCache.subscribe(key, () => fetcherRef.current(), onStoreChange, intervalMs);
    },
    [key, enabled, intervalMs]
  );

  const getSnapshot = useCallback(
    () => (enabled ? liveCache.getSnapshot<T>(key) : (IDLE as QuerySnapshot<T>)),
    [key, enabled]
  );

  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const refetch = useCallback(() => {
    if (enabled) liveCache.refetch(key);
  }, [key, enabled]);

  return {
    ...snapshot,
    isStale: snapshot.status === "success" && snapshot.error != null,
    refetch,
  };
}
