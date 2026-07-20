import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Loader2, RefreshCw } from "lucide-react";
import {
  ApiError,
  getAccount,
  getFundamentals,
  getPositions,
  type AccountInfo,
  type FundamentalsSnapshot,
  type Position,
} from "../lib/api";
import {
  accountDayPnL,
  investedAmount,
  positionDayPnL,
  positionTotalPnL,
  totalUnrealizedPl,
} from "../lib/portfolio";
import SectionCard from "../components/SectionCard";
import StatTile from "../components/StatTile";
import NextAction from "../components/NextAction";
import WorkflowBar from "../components/trade/WorkflowBar";
import type { PageId } from "../types/nav";

interface PortfolioPageProps {
  onTrade: (symbol: string) => void;
  onViewAnalysis: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
}

// Refresh the live account balance + positions every 15s while the page and
// browser tab are visible. Polling pauses when the tab is hidden and backs off
// exponentially (capped) when the broker rate-limits us with a 429.
const POLL_MS = 15_000;
const MAX_BACKOFF_STEPS = 3; // 15s -> 30s -> 60s -> 120s cap

// Sector fundamentals barely change intraday, so they are cached per symbol for
// the life of the page ("cache the account list") — the 15s poll re-fetches the
// balance and positions, but reuses this cache instead of re-hitting the
// fundamentals endpoint for symbols we've already resolved. Only successful
// lookups are cached, so a transient failure retries on the next symbol change.
const _fundamentalsCache = new Map<string, FundamentalsSnapshot>();

async function getFundamentalsCached(symbol: string): Promise<FundamentalsSnapshot> {
  const hit = _fundamentalsCache.get(symbol);
  if (hit) return hit;
  const snapshot = await getFundamentals(symbol);
  _fundamentalsCache.set(symbol, snapshot);
  return snapshot;
}

function money(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function pct(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/**
 * Live account + positions with visible-tab polling.
 *
 * Guarantees:
 *  - refreshes every 15s only while `document.visibilityState === "visible"`;
 *  - pauses when the tab is hidden and refreshes immediately on return;
 *  - backs off (2^n, capped) after a 429 and resets on the next success;
 *  - never clears the last good values — a failed refresh keeps them on screen
 *    and only surfaces `error`.
 */
function usePortfolioData() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true); // true only until the first result
  const [refreshing, setRefreshing] = useState(false); // a background/manual refresh is in flight
  const [error, setError] = useState<ApiError | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const inFlight = useRef(false);
  const backoff = useRef(0); // consecutive 429s
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Latest fetch fn, so the scheduler/visibility listener never call a stale closure.
  const fetchRef = useRef<(manual?: boolean) => void>(() => {});

  const clearTimer = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = undefined;
    }
  }, []);

  const scheduleNext = useCallback(() => {
    clearTimer();
    if (document.visibilityState !== "visible") return; // stay paused while hidden
    const delay = POLL_MS * 2 ** Math.min(backoff.current, MAX_BACKOFF_STEPS);
    timer.current = setTimeout(() => fetchRef.current(), delay);
  }, [clearTimer]);

  const runFetch = useCallback(
    async (manual = false) => {
      // Don't poll a hidden tab; a manual click still refreshes.
      if (!manual && document.visibilityState !== "visible") return;
      if (inFlight.current) return; // never overlap requests
      inFlight.current = true;
      setRefreshing(true);
      try {
        const [a, p] = await Promise.all([getAccount(), getPositions()]);
        setAccount(a);
        setPositions(p);
        setLastUpdated(new Date());
        setError(null);
        backoff.current = 0; // recovered — resume the fast cadence
      } catch (err) {
        // Keep the last successful account/positions on screen; just report it.
        setError(err instanceof ApiError ? err : new ApiError(0, "Unexpected error."));
        if (err instanceof ApiError && err.status === 429) {
          backoff.current = Math.min(backoff.current + 1, MAX_BACKOFF_STEPS);
        }
      } finally {
        inFlight.current = false;
        setRefreshing(false);
        setLoading(false);
        scheduleNext();
      }
    },
    [scheduleNext]
  );

  useEffect(() => {
    fetchRef.current = runFetch;
  }, [runFetch]);

  useEffect(() => {
    fetchRef.current(); // initial load
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        fetchRef.current(); // catch up immediately, then resume the interval
      } else {
        clearTimer(); // pause while hidden
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimer();
    };
  }, [clearTimer]);

  const refresh = useCallback(() => fetchRef.current(true), []);

  return { account, positions, loading, refreshing, error, lastUpdated, refresh };
}

export default function PortfolioPage({
  onTrade,
  onViewAnalysis,
  onNavigate,
}: PortfolioPageProps) {
  const { account, positions, loading, refreshing, error, lastUpdated, refresh } =
    usePortfolioData();

  // Real sector exposure from holdings: sector comes from provider fundamentals
  // (cached per symbol), weighted by each position's current market value. The
  // weighting is recomputed on every poll (cheap, local) so it tracks live
  // values, while the fundamentals lookups themselves are cached. null = not
  // resolved yet; {} once at least one lookup has completed.
  const [sectorBySymbol, setSectorBySymbol] = useState<Record<string, string> | null>(null);

  // Only re-resolve fundamentals when the SET of held symbols changes, not on
  // every 15s poll (which produces a fresh `positions` array each time).
  const symbolKey = useMemo(
    () =>
      positions
        .map((p) => p.symbol)
        .sort()
        .join(","),
    [positions]
  );

  useEffect(() => {
    const symbols = symbolKey ? symbolKey.split(",") : [];
    if (symbols.length === 0) {
      setSectorBySymbol(null);
      return;
    }
    let alive = true;
    Promise.allSettled(symbols.map((s) => getFundamentalsCached(s))).then((results) => {
      if (!alive) return;
      const next: Record<string, string> = {};
      symbols.forEach((symbol, i) => {
        const r = results[i];
        next[symbol] = r.status === "fulfilled" && r.value.sector ? r.value.sector : "Unknown";
      });
      setSectorBySymbol(next);
    });
    return () => {
      alive = false;
    };
  }, [symbolKey]);

  const sectorExposure = useMemo(() => {
    if (positions.length === 0 || sectorBySymbol === null) return null;
    const total = positions.reduce((s, p) => s + Math.abs(Number(p.market_value) || 0), 0);
    const bySector = new Map<string, number>();
    positions.forEach((p) => {
      const mv = Math.abs(Number(p.market_value) || 0);
      const sector = sectorBySymbol[p.symbol] ?? "Unknown";
      bySector.set(sector, (bySector.get(sector) ?? 0) + mv);
    });
    return [...bySector.entries()]
      .map(([sector, mv]) => ({ sector, pct: total ? (mv / total) * 100 : 0 }))
      .sort((a, b) => b.pct - a.pct);
  }, [positions, sectorBySymbol]);

  const concentrationPct = useMemo(() => {
    const values = positions.map((p) => Math.abs(Number(p.market_value) || 0));
    const total = values.reduce((s, v) => s + v, 0);
    return total ? (Math.max(...values) / total) * 100 : 0;
  }, [positions]);

  const dayPl = useMemo(() => (account ? accountDayPnL(account) : null), [account]);
  const invested = useMemo(() => (account ? investedAmount(account) : 0), [account]);
  const totalPl = useMemo(() => totalUnrealizedPl(positions), [positions]);

  const largest = useMemo(() => {
    if (positions.length === 0) return { winner: null, loser: null };
    const sorted = [...positions].sort(
      (a, b) => Number(b.unrealized_pl) - Number(a.unrealized_pl)
    );
    return { winner: sorted[0], loser: sorted[sorted.length - 1] };
  }, [positions]);

  // Initial failure (no data yet) is blocking; a failure while we already have
  // data is non-blocking — we keep showing the last good values.
  const staleError = error && account;

  return (
    <div className="space-y-6">
      <WorkflowBar current={4} onNavigate={onNavigate} />
      <NextAction
        step="Next step · Review your process"
        prompt="Close the loop — review your trade journal and performance analytics to see if you're following a profitable process."
        label="Review Journal"
        icon={<BookOpen size={15} />}
        onClick={() => onNavigate("analysis")}
        secondary={{ label: "Scan the market", onClick: () => onNavigate("markets") }}
      />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink-primary">Portfolio</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Live account and position data from the connected paper-trading broker. Auto-refreshes
            every 15 seconds while this tab is open.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium text-ink-primary transition hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            Refresh
          </button>
          {lastUpdated && (
            <span className="text-xs tabular-nums text-ink-muted">
              Updated {formatTime(lastUpdated)}
            </span>
          )}
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <Loader2 size={16} className="animate-spin" /> Loading account…
        </div>
      )}

      {/* Blocking error: initial load failed and there's nothing to show. */}
      {error && !account && (
        <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2.5 text-sm text-status-critical">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{error.message}</span>
        </div>
      )}

      {/* Non-blocking notice: a refresh failed but the last values are still shown. */}
      {staleError && (
        <div className="flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning-soft px-3 py-2.5 text-sm text-status-warning">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            {error.status === 429
              ? "Broker is rate-limiting refreshes — showing the last values and slowing down."
              : "Couldn't refresh just now — showing the last successful values."}
          </span>
        </div>
      )}

      {account && !loading && dayPl && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Total account value" value={money(Number(account.equity))} />
          <StatTile label="Available cash" value={money(Number(account.cash))} />
          <StatTile label="Invested" value={money(invested)} />
          <StatTile label="Buying power" value={money(Number(account.buying_power))} />
          <StatTile
            label="Today's P/L"
            value={money(dayPl.dollar)}
            sub={pct(dayPl.pct)}
            tone={dayPl.dollar >= 0 ? "good" : "critical"}
          />
          <StatTile
            label="Total P/L"
            value={money(totalPl)}
            tone={totalPl >= 0 ? "good" : "critical"}
          />
          <StatTile label="Largest winner" value={largest.winner?.symbol ?? "—"} />
          <StatTile label="Largest loser" value={largest.loser?.symbol ?? "—"} />
        </div>
      )}

      <SectionCard title="Positions">
        {positions.length === 0 && !loading ? (
          <p className="text-sm text-ink-secondary">No open positions.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
                  <th className="py-2 pr-3 font-medium">Ticker</th>
                  <th className="py-2 pr-3 font-medium">Shares</th>
                  <th className="py-2 pr-3 font-medium">Avg entry</th>
                  <th className="py-2 pr-3 font-medium">Current</th>
                  <th className="py-2 pr-3 font-medium">Market value</th>
                  <th className="py-2 pr-3 font-medium">Today's P/L</th>
                  <th className="py-2 pr-3 font-medium">Total P/L</th>
                  <th className="py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const day = positionDayPnL(p);
                  const total = positionTotalPnL(p);
                  return (
                    <tr key={p.symbol} className="border-b border-border last:border-0">
                      <td className="py-2.5 pr-3 font-medium text-ink-primary">{p.symbol}</td>
                      <td className="py-2.5 pr-3 text-ink-secondary">{p.qty}</td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {money(Number(p.avg_entry_price))}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {money(Number(p.current_price))}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {money(Number(p.market_value))}
                      </td>
                      <td
                        className={`py-2.5 pr-3 font-medium ${
                          day.dollar >= 0 ? "text-status-good" : "text-status-critical"
                        }`}
                      >
                        {money(day.dollar)} ({pct(day.pct)})
                      </td>
                      <td
                        className={`py-2.5 pr-3 font-medium ${
                          total.dollar >= 0 ? "text-status-good" : "text-status-critical"
                        }`}
                      >
                        {money(total.dollar)} ({pct(total.pct)})
                      </td>
                      <td className="py-2.5">
                        <div className="flex flex-wrap gap-2 text-xs">
                          <button
                            onClick={() => onTrade(p.symbol)}
                            className="font-medium text-brand-blue hover:underline"
                          >
                            Buy more
                          </button>
                          <button
                            onClick={() => onTrade(p.symbol)}
                            className="font-medium text-status-critical hover:underline"
                          >
                            Sell
                          </button>
                          <button
                            onClick={() => onViewAnalysis(p.symbol)}
                            className="font-medium text-ink-secondary hover:text-ink-primary"
                          >
                            Analysis
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Portfolio Intelligence"
        description="Sector exposure and concentration computed from your actual holdings"
      >
        {positions.length === 0 ? (
          <p className="text-sm text-ink-muted">Insufficient data — no positions to analyze.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <StatTile label="Holdings" value={String(positions.length)} />
              <StatTile
                label="Largest position"
                value={`${concentrationPct.toFixed(0)}%`}
                tone={concentrationPct >= 40 ? "critical" : undefined}
              />
              <StatTile label="Invested" value={money(invested)} />
            </div>

            {sectorExposure === null ? (
              <p className="mt-3 text-sm text-ink-muted">Loading sector exposure…</p>
            ) : sectorExposure.length === 0 ? (
              <p className="mt-3 text-sm text-ink-muted">Sector data unavailable for these holdings.</p>
            ) : (
              <div className="mt-3 space-y-2">
                {sectorExposure.map((s) => (
                  <div key={s.sector}>
                    <div className="flex justify-between text-xs text-ink-secondary">
                      <span>{s.sector}</span>
                      <span className="tabular-nums">{s.pct.toFixed(0)}%</span>
                    </div>
                    <div className="mt-0.5 h-1.5 overflow-hidden rounded bg-surface-2">
                      <div
                        className="h-full rounded bg-brand-blue"
                        style={{ width: `${Math.min(100, s.pct)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="mt-3 text-[11px] text-ink-muted">
              Sector from provider fundamentals, weighted by market value. Beta, correlation, and
              volatility require a risk engine that isn't built yet.
            </p>
          </>
        )}
      </SectionCard>
    </div>
  );
}
