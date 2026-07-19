import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BookOpen, Loader2 } from "lucide-react";
import {
  ApiError,
  getAccount,
  getFundamentals,
  getPositions,
  type AccountInfo,
  type Position,
} from "../lib/api";
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

function money(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function pct(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export default function PortfolioPage({
  onTrade,
  onViewAnalysis,
  onNavigate,
}: PortfolioPageProps) {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([getAccount(), getPositions()])
      .then(([a, p]) => {
        setAccount(a);
        setPositions(p);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err : new ApiError(0, "Unexpected error."));
      })
      .finally(() => setLoading(false));
  }, []);

  // Real sector exposure from holdings: sector comes from provider
  // fundamentals, weighted by each position's market value. null = loading,
  // [] = attempted but no sector data (rendered as "Insufficient data").
  const [sectorExposure, setSectorExposure] = useState<{ sector: string; pct: number }[] | null>(
    null
  );
  useEffect(() => {
    if (positions.length === 0) {
      setSectorExposure(null);
      return;
    }
    let alive = true;
    const total = positions.reduce((s, p) => s + Math.abs(Number(p.market_value) || 0), 0);
    Promise.allSettled(positions.map((p) => getFundamentals(p.symbol)))
      .then((results) => {
        if (!alive) return;
        const bySector = new Map<string, number>();
        results.forEach((r, i) => {
          const mv = Math.abs(Number(positions[i].market_value) || 0);
          const sector = r.status === "fulfilled" && r.value.sector ? r.value.sector : "Unknown";
          bySector.set(sector, (bySector.get(sector) ?? 0) + mv);
        });
        setSectorExposure(
          [...bySector.entries()]
            .map(([sector, mv]) => ({ sector, pct: total ? (mv / total) * 100 : 0 }))
            .sort((a, b) => b.pct - a.pct)
        );
      })
      .catch(() => {
        if (alive) setSectorExposure([]);
      });
    return () => {
      alive = false;
    };
  }, [positions]);

  const concentrationPct = useMemo(() => {
    const values = positions.map((p) => Math.abs(Number(p.market_value) || 0));
    const total = values.reduce((s, v) => s + v, 0);
    return total ? (Math.max(...values) / total) * 100 : 0;
  }, [positions]);

  const totals = useMemo(() => {
    const equity = account ? Number(account.equity) : 0;
    const lastEquity = account ? Number(account.last_equity) : 0;
    const cash = account ? Number(account.cash) : 0;
    const invested = equity - cash;
    const dayPl = equity - lastEquity;
    const dayPlPct = lastEquity ? (dayPl / lastEquity) * 100 : 0;
    const totalPl = positions.reduce((sum, p) => sum + Number(p.unrealized_pl), 0);
    return { equity, cash, invested, dayPl, dayPlPct, totalPl };
  }, [account, positions]);

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
      <div>
        <h1 className="text-xl font-semibold text-ink-primary">Portfolio</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Live account and position data from the connected paper-trading broker.
        </p>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <Loader2 size={16} className="animate-spin" /> Loading account…
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2.5 text-sm text-status-critical">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{error.message}</span>
        </div>
      )}

      {account && !loading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Total account value" value={money(totals.equity)} />
          <StatTile label="Available cash" value={money(totals.cash)} />
          <StatTile label="Invested" value={money(totals.invested)} />
          <StatTile label="Buying power" value={money(Number(account.buying_power))} />
          <StatTile
            label="Today's P/L"
            value={money(totals.dayPl)}
            sub={pct(totals.dayPlPct)}
            tone={totals.dayPl >= 0 ? "good" : "critical"}
          />
          <StatTile
            label="Total P/L"
            value={money(totals.totalPl)}
            tone={totals.totalPl >= 0 ? "good" : "critical"}
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
                  <th className="py-2 pr-3 font-medium">Today</th>
                  <th className="py-2 pr-3 font-medium">Total P/L</th>
                  <th className="py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const unrealizedPl = Number(p.unrealized_pl);
                  const unrealizedPlPct = Number(p.unrealized_plpc) * 100;
                  const changeToday = Number(p.change_today) * 100;
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
                          changeToday >= 0 ? "text-status-good" : "text-status-critical"
                        }`}
                      >
                        {pct(changeToday)}
                      </td>
                      <td
                        className={`py-2.5 pr-3 font-medium ${
                          unrealizedPl >= 0 ? "text-status-good" : "text-status-critical"
                        }`}
                      >
                        {money(unrealizedPl)} ({pct(unrealizedPlPct)})
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
              <StatTile label="Invested" value={money(totals.invested)} />
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
