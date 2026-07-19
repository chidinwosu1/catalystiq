import { useMemo, useState } from "react";
import { ArrowRight, Globe, Zap } from "lucide-react";
import DemoBadge from "../components/DemoBadge";
import RatingBadge from "../components/RatingBadge";
import RecentActivity from "../components/dashboard/RecentActivity";
import WorkflowBar from "../components/trade/WorkflowBar";
import OpportunityPanel from "../components/trade/OpportunityPanel";
import MarketOverviewPanel from "../components/trade/MarketOverviewPanel";
import { opportunities, type OpportunityDetail } from "../mockTradeCenter";
import { riskRole, roleClasses } from "../lib/theme";
import { useQuotes } from "../lib/useQuotes";
import type { RiskLevel } from "../mockDashboardData";
import type { PageId } from "../types/nav";

interface TradeCenterPageProps {
  onTrade: (symbol: string) => void;
  onViewAnalysis: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
}

type SortKey = "pop" | "risk";
type RiskFilter = "all" | "Low" | "Moderate";

const RISK_ORDER: Record<RiskLevel, number> = { Low: 0, Moderate: 1, Elevated: 2, High: 3 };
const SORTS: { key: SortKey; label: string }[] = [
  { key: "pop", label: "Prob. of profit" },
  { key: "risk", label: "Lowest risk" },
];
const FILTERS: { key: RiskFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "Low", label: "Low risk" },
  { key: "Moderate", label: "Moderate" },
];

function OpportunityCard({
  opp,
  livePrice,
  onReview,
  onTrade,
}: {
  opp: OpportunityDetail;
  livePrice: number | null;
  onReview: () => void;
  onTrade: () => void;
}) {
  const risk = roleClasses[riskRole(opp.risk)];
  return (
    <div
      onClick={onReview}
      className="cq-glass flex cursor-pointer flex-col rounded-[18px] p-[18px] transition-all hover:-translate-y-1 hover:border-brand-blue/40"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[18px] font-bold tracking-tight text-ink-primary">{opp.symbol}</div>
          <div className="mt-px text-[11.5px] text-ink-muted">{opp.companyName}</div>
          <div className="mt-1 font-mono text-[12.5px] text-ink-secondary">
            {livePrice != null ? (
              <>
                {livePrice.toLocaleString("en-US", { style: "currency", currency: "USD" })}{" "}
                <span className="text-[#5ea8ff]">live</span>
              </>
            ) : (
              <span className="text-ink-muted">{opp.price}</span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <RatingBadge rating={opp.rating} />
          <div className="text-right">
            <span className="flex items-center gap-1 font-mono text-[15px] font-bold text-[#5ea8ff]">
              <Zap size={13} /> {opp.catalystScore}
            </span>
            <div className="text-[9.5px] uppercase tracking-wide text-ink-muted">Catalyst</div>
          </div>
        </div>
      </div>

      <div className="mt-3.5 grid grid-cols-2 gap-x-3 gap-y-2.5 text-[12px]">
        <div>
          <div className="text-ink-muted">Prob. of profit</div>
          <div className="mt-px font-semibold text-ink-primary">{opp.probabilityOfProfit}%</div>
        </div>
        <div>
          <div className="text-ink-muted">Expected return</div>
          <div className="mt-px font-semibold text-status-good">{opp.expectedReturn}</div>
        </div>
        <div>
          <div className="text-ink-muted">Risk</div>
          <div className={`mt-px font-semibold ${risk.text}`}>{opp.risk}</div>
        </div>
        <div>
          <div className="text-ink-muted">Holding period</div>
          <div className="mt-px font-semibold text-ink-primary">{opp.holdingPeriod}</div>
        </div>
      </div>

      <div className="mt-3.5 rounded-xl border border-border bg-white/[0.02] px-3 py-2.5">
        <div className="text-[9.5px] uppercase tracking-wide text-ink-muted">Primary catalyst</div>
        <div className="mt-0.5 text-[12.5px] text-ink-secondary">{opp.primaryCatalyst}</div>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onReview();
          }}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-brand-blue px-3.5 py-2.5 text-[13px] font-semibold text-white transition-colors hover:bg-brand-blue/90"
        >
          Review opportunity
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onTrade();
          }}
          className="rounded-xl border border-border-strong px-3.5 py-2.5 text-[13px] font-semibold text-ink-secondary transition-colors hover:border-brand-blue hover:text-ink-primary"
        >
          Trade
        </button>
      </div>
    </div>
  );
}

export default function TradeCenterPage({
  onTrade,
  onViewAnalysis,
  onNavigate,
}: TradeCenterPageProps) {
  const [sortKey, setSortKey] = useState<SortKey>("pop");
  const [filter, setFilter] = useState<RiskFilter>("all");
  const [showAll, setShowAll] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [marketOpen, setMarketOpen] = useState(false);
  const [marketExpanded, setMarketExpanded] = useState(false);

  // Live quotes for every opportunity; falls back to the demo price per card.
  const { prices } = useQuotes(useMemo(() => opportunities.map((o) => o.symbol), []));

  const sorted = useMemo(() => {
    const list = opportunities.filter((o) => filter === "all" || o.risk === filter);
    return [...list].sort((a, b) => {
      if (sortKey === "pop") return b.probabilityOfProfit - a.probabilityOfProfit;
      return RISK_ORDER[a.risk] - RISK_ORDER[b.risk] || b.catalystScore - a.catalystScore;
    });
  }, [sortKey, filter]);

  const visible = showAll ? sorted : sorted.slice(0, 4);

  function openReview(opp: OpportunityDetail) {
    setMarketOpen(false);
    setSelected(opportunities.indexOf(opp));
  }
  function closeReview() {
    setSelected(null);
    setExpanded(false);
  }
  function openMarket() {
    setSelected(null);
    setMarketOpen(true);
  }

  return (
    <div>
      <WorkflowBar current={1} onNavigate={onNavigate} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-[#5ea8ff]">
            Trade Center
          </span>
          <h1 className="mt-2 text-[clamp(26px,3vw,34px)] font-bold tracking-[-0.025em] text-ink-primary">
            Highest-conviction opportunities
          </h1>
          <p className="mt-1 max-w-[60ch] text-[14.5px] text-ink-secondary">
            The model's strongest setups right now, matched to your preferences. Review any one for
            the full thesis — the list stays right where you left it.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={openMarket}
            className="cq-glass inline-flex items-center gap-2 rounded-xl px-3.5 py-2.5 text-[13px] font-semibold text-ink-primary transition-transform hover:-translate-y-0.5"
          >
            <Globe size={16} className="text-[#5ea8ff]" />
            Market Overview
          </button>
          <DemoBadge />
        </div>
      </div>

      {/* Controls */}
      <div className="my-5 flex flex-wrap items-center gap-2.5">
        <div className="flex gap-0.5 rounded-xl border border-border bg-surface p-0.5">
          {SORTS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSortKey(s.key)}
              className={`rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors ${
                sortKey === s.key
                  ? "bg-surface-3 text-ink-primary"
                  : "text-ink-secondary hover:text-ink-primary"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => {
              setFilter(f.key);
              setShowAll(true);
            }}
            className={`rounded-full border px-3 py-1.5 text-[12.5px] transition-colors ${
              filter === f.key
                ? "border-brand-blue/45 bg-brand-blue/10 text-ink-primary"
                : "border-border bg-surface text-ink-secondary hover:text-ink-primary"
            }`}
          >
            {f.label}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => setShowAll((v) => !v)}
          className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-[#5ea8ff] hover:underline"
        >
          {showAll ? "Show top 4 only" : "View all opportunities"}
          <ArrowRight size={14} />
        </button>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {visible.map((opp) => (
          <OpportunityCard
            key={opp.symbol}
            opp={opp}
            livePrice={prices[opp.symbol] ?? null}
            onReview={() => openReview(opp)}
            onTrade={() => onTrade(opp.symbol)}
          />
        ))}
      </div>

      {/* Recent activity */}
      <div className="mt-10">
        <RecentActivity onResume={onViewAnalysis} />
      </div>

      <OpportunityPanel
        opp={selected !== null ? opportunities[selected] : null}
        livePrice={selected !== null ? prices[opportunities[selected].symbol] ?? null : null}
        expanded={expanded}
        onClose={closeReview}
        onToggleExpand={() => setExpanded((v) => !v)}
        onTrade={(sym) => {
          closeReview();
          onTrade(sym);
        }}
        onAnalyze={(sym) => {
          closeReview();
          onViewAnalysis(sym);
        }}
      />

      <MarketOverviewPanel
        open={marketOpen}
        expanded={marketExpanded}
        onClose={() => {
          setMarketOpen(false);
          setMarketExpanded(false);
        }}
        onToggleExpand={() => setMarketExpanded((v) => !v)}
      />
    </div>
  );
}
