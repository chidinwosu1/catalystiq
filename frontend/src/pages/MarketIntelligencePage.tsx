import { useEffect, useState } from "react";
import { LineChart, TrendingDown, TrendingUp } from "lucide-react";
import SectionCard from "../components/SectionCard";
import DemoBadge from "../components/DemoBadge";
import RatingBadge from "../components/RatingBadge";
import NextAction from "../components/NextAction";
import BehavioralAnalysisTable from "../components/BehavioralAnalysisTable";
import WorkflowBar from "../components/trade/WorkflowBar";
import { catalysts, dailyWatchlist } from "../mockMarketData";
import { marketWideBehavioralAnalysis } from "../mockBehavioralData";
import { getQuotes, getSectors, type QuoteResult, type SectorPerformance } from "../lib/api";
import type { Rating } from "../types";
import type { PageId } from "../types/nav";

// Live market-overview indices/rates -> Yahoo symbols. `pct` marks a rate
// (10-yr yield) shown with a % suffix. Values are fetched live, not mocked.
const MARKET_OVERVIEW: { label: string; symbol: string; pct?: boolean }[] = [
  { label: "S&P 500", symbol: "^GSPC" },
  { label: "Nasdaq", symbol: "^IXIC" },
  { label: "Dow", symbol: "^DJI" },
  { label: "Russell 2000", symbol: "^RUT" },
  { label: "VIX", symbol: "^VIX" },
  { label: "10-Year Treasury", symbol: "^TNX", pct: true },
  { label: "US Dollar Index", symbol: "DX-Y.NYB" },
  { label: "Oil (WTI)", symbol: "CL=F" },
  { label: "Gold", symbol: "GC=F" },
];

interface MarketIntelligencePageProps {
  onTrade: (symbol: string) => void;
  onViewAnalysis: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
}

function pctText(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function pctClass(v: number | null): string {
  if (v === null || v === undefined) return "text-ink-muted";
  return v >= 0 ? "text-status-good" : "text-status-critical";
}

const STATUS_CLASS: Record<string, string> = {
  Confirmed: "border-status-good/40 bg-status-good-soft text-status-good",
  Proposed: "border-status-warning/40 bg-status-warning-soft text-status-warning",
  Speculation: "border-status-neutral/40 bg-status-neutral-soft text-status-neutral",
};

export default function MarketIntelligencePage({
  onTrade,
  onViewAnalysis,
  onNavigate,
}: MarketIntelligencePageProps) {
  const topName = dailyWatchlist[0]?.symbol ?? "NVDA";

  // Live market overview (real quotes; unavailable symbols show "Insufficient data").
  const [overview, setOverview] = useState<QuoteResult[]>([]);
  useEffect(() => {
    let alive = true;
    getQuotes(MARKET_OVERVIEW.map((m) => m.symbol))
      .then((q) => {
        if (alive) setOverview(q);
      })
      .catch(() => {
        /* leave empty; render shows "Insufficient data", never fabricated */
      });
    return () => {
      alive = false;
    };
  }, []);
  const overviewBySymbol = new Map(overview.map((q) => [q.symbol.toUpperCase(), q]));

  // Live sector performance (deterministic, from real ETF history).
  const [sectors, setSectors] = useState<SectorPerformance[] | null>(null);
  useEffect(() => {
    let alive = true;
    getSectors()
      .then((s) => {
        if (alive) setSectors(s);
      })
      .catch(() => {
        if (alive) setSectors([]);
      });
    return () => {
      alive = false;
    };
  }, []);
  const rankedSectors = (sectors ?? [])
    .slice()
    .sort((a, b) => (b.rel_strength_vs_spy ?? -Infinity) - (a.rel_strength_vs_spy ?? -Infinity));

  return (
    <div className="space-y-6">
      <WorkflowBar current={1} onNavigate={onNavigate} />
      <NextAction
        step="Next step · Research a candidate"
        prompt={`${topName} tops today's watchlist. Dig into the full research before you commit capital.`}
        label={`Research ${topName}`}
        icon={<LineChart size={15} />}
        onClick={() => onViewAnalysis(topName)}
        secondary={{ label: `Trade ${topName}`, onClick: () => onTrade(topName) }}
      />
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink-primary">Market Analysis</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Market Overview below is live. Sector rankings, catalysts, and the watchlist still
            use illustrative demo data until those modules are wired.
          </p>
        </div>
        <DemoBadge />
      </div>

      <SectionCard title="Market Overview" description="Live index, rate, and commodity levels">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {MARKET_OVERVIEW.map(({ label, symbol, pct }) => {
            const q = overviewBySymbol.get(symbol.toUpperCase());
            const ok = q && q.status === "ok" && q.price !== null;
            const cp = ok ? q!.change_pct : null;
            return (
              <div key={symbol} className="rounded-lg border border-border px-3 py-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-ink-primary">{label}</span>
                  {cp !== null && cp !== undefined ? (
                    <span
                      className={`flex items-center gap-1 text-sm font-semibold ${
                        cp >= 0 ? "text-status-good" : "text-status-critical"
                      }`}
                    >
                      {cp >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                      {cp >= 0 ? "+" : ""}
                      {cp.toFixed(2)}%
                    </span>
                  ) : (
                    <span className="text-xs text-ink-muted">—</span>
                  )}
                </div>
                <p className="mt-0.5 text-lg font-semibold text-ink-primary">
                  {ok ? (
                    pct
                      ? `${q!.price!.toFixed(2)}%`
                      : q!.price!.toLocaleString(undefined, { maximumFractionDigits: 2 })
                  ) : (
                    <span className="text-sm font-normal text-ink-muted">Insufficient data</span>
                  )}
                </p>
              </div>
            );
          })}
        </div>
      </SectionCard>

      <SectionCard
        title="Industry Sector Ranking"
        description="SPDR sector ETFs, ranked by 1-week relative strength vs SPY (computed from real prices)"
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
                <th className="py-2 pr-3 font-medium">Sector</th>
                <th className="py-2 pr-3 font-medium">ETF</th>
                <th className="py-2 pr-3 font-medium">1D</th>
                <th className="py-2 pr-3 font-medium">1W</th>
                <th className="py-2 font-medium">Rel. vs SPY</th>
              </tr>
            </thead>
            <tbody>
              {sectors === null ? (
                <tr>
                  <td colSpan={5} className="py-3 text-ink-muted">
                    Loading…
                  </td>
                </tr>
              ) : (
                rankedSectors.map((s) => (
                  <tr key={s.symbol} className="border-b border-border last:border-0">
                    <td className="py-2.5 pr-3 font-medium text-ink-primary">{s.sector}</td>
                    <td className="py-2.5 pr-3 text-ink-muted">{s.symbol}</td>
                    {s.status !== "ok" ? (
                      <td colSpan={3} className="py-2.5 text-ink-muted">
                        Insufficient data
                      </td>
                    ) : (
                      <>
                        <td className={`py-2.5 pr-3 ${pctClass(s.daily_pct)}`}>{pctText(s.daily_pct)}</td>
                        <td className={`py-2.5 pr-3 ${pctClass(s.weekly_pct)}`}>{pctText(s.weekly_pct)}</td>
                        <td className={`py-2.5 font-medium ${pctClass(s.rel_strength_vs_spy)}`}>
                          {pctText(s.rel_strength_vs_spy)}
                        </td>
                      </>
                    )}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="Economic and Political Catalysts">
        <ul className="space-y-3">
          {catalysts.map((c) => (
            <li key={c.headline} className="rounded-lg border border-border px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                  {c.category}
                </span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${STATUS_CLASS[c.status]}`}
                >
                  {c.status}
                </span>
              </div>
              <p className="mt-1 text-sm font-medium text-ink-primary">{c.headline}</p>
              <p className="mt-1 text-xs text-ink-secondary">{c.transmissionPath}</p>
            </li>
          ))}
        </ul>
      </SectionCard>

      <BehavioralAnalysisTable
        title="Investor Functional Behavior Analysis"
        description="Market-wide, mapped to the catalysts above - how investors are likely to react, and what would push that reaction positively or negatively"
        rows={marketWideBehavioralAnalysis}
      />

      <SectionCard title="Daily Watchlist">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
                <th className="py-2 pr-3 font-medium">Ticker</th>
                <th className="py-2 pr-3 font-medium">Intraday</th>
                <th className="py-2 pr-3 font-medium">Swing</th>
                <th className="py-2 pr-3 font-medium">Confidence</th>
                <th className="py-2 pr-3 font-medium">Bull / Bear</th>
                <th className="py-2 pr-3 font-medium">Expected move</th>
                <th className="py-2 pr-3 font-medium">Catalyst</th>
                <th className="py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {dailyWatchlist.map((w) => (
                <tr key={w.symbol} className="border-b border-border last:border-0">
                  <td className="py-2.5 pr-3 font-medium text-ink-primary">{w.symbol}</td>
                  <td className="py-2.5 pr-3">
                    <RatingBadge rating={w.intradayRating as Rating} />
                  </td>
                  <td className="py-2.5 pr-3">
                    <RatingBadge rating={w.swingRating as Rating} />
                  </td>
                  <td className="py-2.5 pr-3 text-ink-secondary">{w.confidence}</td>
                  <td className="py-2.5 pr-3 text-ink-secondary">
                    {w.bullishPct}% / {w.bearishPct}%
                  </td>
                  <td className="py-2.5 pr-3 text-ink-secondary">{w.expectedMove}</td>
                  <td className="py-2.5 pr-3 text-ink-secondary">{w.catalyst}</td>
                  <td className="py-2.5">
                    <div className="flex gap-2 text-xs">
                      <button
                        onClick={() => onTrade(w.symbol)}
                        className="font-medium text-brand-blue hover:underline"
                      >
                        Trade
                      </button>
                      <button
                        onClick={() => onViewAnalysis(w.symbol)}
                        className="font-medium text-ink-secondary hover:text-ink-primary"
                      >
                        Analysis
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
}
