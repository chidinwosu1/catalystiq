import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BookOpen, Loader2, RefreshCw } from "lucide-react";
import {
  ApiError,
  getFundamentals,
  getOrderHistory,
  type FundamentalsSnapshot,
  type OrderRecord,
} from "../lib/api";
import { useLiveAccount, useLivePositions } from "../lib/liveData";
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

// Sector fundamentals barely change intraday, so they are cached per symbol for
// the life of the page — the shared 15s live poll re-fetches the balance and
// positions, but reuses this cache instead of re-hitting the fundamentals
// endpoint for symbols we've already resolved. Only successful lookups are
// cached, so a transient failure retries on the next symbol change.
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

function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function PortfolioPage({
  onTrade,
  onViewAnalysis,
  onNavigate,
}: PortfolioPageProps) {
  // Live account + positions come from the shared 15s cache: visibility-aware,
  // deduped, backing off on 429s, and reused by any other page that needs them.
  const accountQuery = useLiveAccount();
  const positionsQuery = useLivePositions();

  const account = accountQuery.data ?? null;
  const positions = useMemo(() => positionsQuery.data ?? [], [positionsQuery.data]);

  // "Loading" only for the very first paint (no data yet); background refreshes
  // never blank the screen.
  const loading =
    (accountQuery.status === "loading" && !account) ||
    (positionsQuery.status === "loading" && !positionsQuery.data);
  const refreshing = accountQuery.isFetching || positionsQuery.isFetching;
  const lastUpdatedMs = Math.max(accountQuery.lastUpdated ?? 0, positionsQuery.lastUpdated ?? 0);

  // A failure while we already have data is non-blocking (keep the last values);
  // a first-load failure with nothing to show is blocking.
  const failure = (accountQuery.error ?? positionsQuery.error) as ApiError | undefined;
  const blockingError = failure && !account ? failure : null;
  const staleError = (accountQuery.isStale || positionsQuery.isStale) && account ? failure : null;

  const refresh = () => {
    accountQuery.refetch();
    positionsQuery.refetch();
  };

  // Recently filled orders back the "how did I get here" view alongside the
  // current positions. Fetched independently (one-shot — order history is not a
  // fast-moving live value) so a history failure, or a broker that doesn't
  // support it, never blanks the positions/account view.
  const [filledOrders, setFilledOrders] = useState<OrderRecord[] | null>(null);
  const [ordersError, setOrdersError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setOrdersError(null);
    getOrderHistory({ filledOnly: true })
      .then((orders) => {
        if (alive) setFilledOrders(orders);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setFilledOrders([]);
        setOrdersError(err instanceof ApiError ? err.message : "Could not load order history.");
      });
    return () => {
      alive = false;
    };
  }, []);

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
          {lastUpdatedMs > 0 && (
            <span className="text-xs tabular-nums text-ink-muted">
              Updated {formatTime(lastUpdatedMs)}
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
      {blockingError && (
        <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2.5 text-sm text-status-critical">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{blockingError.message}</span>
        </div>
      )}

      {/* Non-blocking notice: a refresh failed but the last values are still shown. */}
      {staleError && (
        <div className="flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning-soft px-3 py-2.5 text-sm text-status-warning">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            {staleError.status === 429
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
        title="Filled Orders"
        description="Recently executed orders from the connected broker (read-only)"
      >
        {ordersError ? (
          <p className="text-sm text-ink-muted">{ordersError}</p>
        ) : filledOrders === null ? (
          <p className="text-sm text-ink-muted">Loading filled orders…</p>
        ) : filledOrders.length === 0 ? (
          <p className="text-sm text-ink-secondary">No filled orders in the recent window.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
                  <th className="py-2 pr-3 font-medium">Ticker</th>
                  <th className="py-2 pr-3 font-medium">Side</th>
                  <th className="py-2 pr-3 font-medium">Type</th>
                  <th className="py-2 pr-3 font-medium">Filled</th>
                  <th className="py-2 pr-3 font-medium">Avg price</th>
                  <th className="py-2 pr-3 font-medium">Amount</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filledOrders.map((o) => {
                  const isBuy = o.side.toUpperCase().startsWith("B");
                  const amount = Number(o.filled_amount) || 0;
                  return (
                    <tr
                      key={o.client_order_id || o.order_id || `${o.symbol}-${o.created_at}`}
                      className="border-b border-border last:border-0"
                    >
                      <td className="py-2.5 pr-3 font-medium text-ink-primary">{o.symbol}</td>
                      <td
                        className={`py-2.5 pr-3 font-medium ${
                          isBuy ? "text-status-good" : "text-status-critical"
                        }`}
                      >
                        {o.side || "—"}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">{o.order_type || "—"}</td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {o.filled_qty}
                        {Number(o.total_qty) !== Number(o.filled_qty) ? ` / ${o.total_qty}` : ""}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {money(Number(o.avg_fill_price))}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {amount ? money(amount) : "—"}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {o.status === "partially_filled" ? "Partially filled" : "Filled"}
                      </td>
                      <td className="py-2.5">
                        <button
                          onClick={() => onViewAnalysis(o.symbol)}
                          className="text-xs font-medium text-ink-secondary hover:text-ink-primary"
                        >
                          Analysis
                        </button>
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
