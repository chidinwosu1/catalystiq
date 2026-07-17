import { Target } from "lucide-react";
import RegimeHero from "../components/dashboard/RegimeHero";
import MarketBrief from "../components/dashboard/MarketBrief";
import CatalystTimeline from "../components/dashboard/CatalystTimeline";
import PortfolioAlerts from "../components/dashboard/PortfolioAlerts";
import DashboardWatchlist from "../components/dashboard/DashboardWatchlist";
import QuickActions from "../components/dashboard/QuickActions";
import NextAction from "../components/NextAction";
import { convictionOpportunities } from "../mockDashboardData";
import type { PageId } from "../types/nav";

interface HomePageProps {
  onNavigate: (page: PageId) => void;
  onViewAnalysis: (symbol: string) => void;
}

export default function HomePage({ onNavigate, onViewAnalysis }: HomePageProps) {
  const topPick = convictionOpportunities[0];

  return (
    <div className="space-y-5">
      {/* 1 — Market summary hero */}
      <RegimeHero />

      {/* 2 — Today's market brief */}
      <MarketBrief />

      {/* 3 & 4 — Today's catalysts + portfolio alerts */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <CatalystTimeline />
        <PortfolioAlerts />
      </div>

      {/* 5 — Watchlist */}
      <DashboardWatchlist onViewAnalysis={onViewAnalysis} />

      {/* 9 — Quick actions */}
      <QuickActions onNavigate={onNavigate} />

      {/* Workflow hand-off: guide the user into the next step */}
      <NextAction
        step="Next step · Review the top opportunity"
        prompt={`${topPick.symbol} leads today with a catalyst score of ${topPick.catalystScore}. Open the research to see the full thesis.`}
        label={`Research ${topPick.symbol}`}
        icon={<Target size={15} />}
        onClick={() => onViewAnalysis(topPick.symbol)}
        secondary={{ label: "Scan the market", onClick: () => onNavigate("markets") }}
      />
    </div>
  );
}
