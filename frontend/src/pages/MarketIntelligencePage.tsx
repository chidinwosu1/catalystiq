import { LineChart, TrendingDown, TrendingUp } from "lucide-react";
import SectionCard from "../components/SectionCard";
import DemoBadge from "../components/DemoBadge";
import RatingBadge from "../components/RatingBadge";
import NextAction from "../components/NextAction";
import BehavioralAnalysisTable from "../components/BehavioralAnalysisTable";
import WorkflowBar from "../components/trade/WorkflowBar";
import { catalysts, dailyWatchlist, marketOverview, sectorRotation } from "../mockMarketData";
import { marketWideBehavioralAnalysis } from "../mockBehavioralData";
import type { Rating } from "../types";
import type { PageId } from "../types/nav";

interface MarketIntelligencePageProps {
  onTrade: (symbol: string) => void;
  onViewAnalysis: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
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

  return (
    <div className="space-y-6">
      <WorkflowBar current={1} onNavigate={onNavigate} />
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink-primary">Market Analysis</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Daily macro dashboard - sector rankings, catalysts, and the watchlist below use
            illustrative demo data until the Market Environment / Sector / News modules are
            built.
          </p>
        </div>
        <DemoBadge />
      </div>

      <SectionCard title="Market Overview">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {marketOverview.map((row) => (
            <div key={row.symbol} className="rounded-lg border border-border px-3 py-2.5">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-ink-primary">{row.label}</span>
                <span
                  className={`flex items-center gap-1 text-sm font-semibold ${
                    row.changePct >= 0 ? "text-status-good" : "text-status-critical"
                  }`}
                >
                  {row.changePct >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                  {row.changePct >= 0 ? "+" : ""}
                  {row.changePct.toFixed(1)}%
                </span>
              </div>
              <p className="mt-0.5 text-lg font-semibold text-ink-primary">{row.level}</p>
              <p className="mt-1 text-xs text-ink-secondary">{row.interpretation}</p>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Industry Sector Ranking" description="Ranked strongest to weakest by leadership score">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
                <th className="py-2 pr-3 font-medium">Sector</th>
                <th className="py-2 pr-3 font-medium">1D</th>
                <th className="py-2 pr-3 font-medium">1W</th>
                <th className="py-2 pr-3 font-medium">Rel. strength</th>
                <th className="py-2 pr-3 font-medium">Volume</th>
                <th className="py-2 font-medium">Leadership</th>
              </tr>
            </thead>
            <tbody>
              {[...sectorRotation]
                .sort((a, b) => b.leadershipScore - a.leadershipScore)
                .map((s) => (
                  <tr key={s.name} className="border-b border-border last:border-0">
                    <td className="py-2.5 pr-3 font-medium text-ink-primary">{s.name}</td>
                    <td
                      className={`py-2.5 pr-3 ${
                        s.dailyPct >= 0 ? "text-status-good" : "text-status-critical"
                      }`}
                    >
                      {s.dailyPct >= 0 ? "+" : ""}
                      {s.dailyPct.toFixed(1)}%
                    </td>
                    <td
                      className={`py-2.5 pr-3 ${
                        s.weeklyPct >= 0 ? "text-status-good" : "text-status-critical"
                      }`}
                    >
                      {s.weeklyPct >= 0 ? "+" : ""}
                      {s.weeklyPct.toFixed(1)}%
                    </td>
                    <td className="py-2.5 pr-3 text-ink-secondary">{s.relativeStrength}</td>
                    <td className="py-2.5 pr-3 text-ink-secondary">{s.volume}</td>
                    <td className="py-2.5 font-medium text-ink-primary">{s.leadershipScore}</td>
                  </tr>
                ))}
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

      <NextAction
        step="Next step · Research a candidate"
        prompt={`${topName} tops today's watchlist. Dig into the full research before you commit capital.`}
        label={`Research ${topName}`}
        icon={<LineChart size={15} />}
        onClick={() => onViewAnalysis(topName)}
        secondary={{ label: `Trade ${topName}`, onClick: () => onTrade(topName) }}
      />
    </div>
  );
}
