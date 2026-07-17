import { ArrowRight, Target, Zap } from "lucide-react";
import DemoBadge from "../DemoBadge";
import RatingBadge from "../RatingBadge";
import { convictionOpportunities, type ConvictionOpportunity } from "../../mockDashboardData";
import { riskRole, roleClasses } from "../../lib/theme";

function OpportunityCard({
  opp,
  onReview,
}: {
  opp: ConvictionOpportunity;
  onReview: (symbol: string) => void;
}) {
  const risk = roleClasses[riskRole(opp.risk)];
  return (
    <div className="flex flex-col rounded-xl border border-border bg-surface p-4 transition-colors hover:border-brand-blue/40">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold text-ink-primary">{opp.symbol}</span>
            <RatingBadge rating={opp.recommendation} />
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-secondary">{opp.companyName}</p>
        </div>
        <div className="flex flex-col items-end">
          <span className="flex items-center gap-1 text-xs font-semibold text-brand-blue">
            <Zap size={12} /> {opp.catalystScore}
          </span>
          <span className="text-[10px] uppercase tracking-wide text-ink-muted">Catalyst</span>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div>
          <p className="text-ink-muted">Prob. of profit</p>
          <p className="font-semibold text-ink-primary">{opp.probabilityOfProfit}%</p>
        </div>
        <div>
          <p className="text-ink-muted">Expected return</p>
          <p className="font-semibold text-status-good">{opp.expectedReturn}</p>
        </div>
        <div>
          <p className="text-ink-muted">Risk</p>
          <p className={`font-semibold ${risk.text}`}>{opp.risk}</p>
        </div>
        <div>
          <p className="text-ink-muted">Holding period</p>
          <p className="font-semibold text-ink-primary">{opp.holdingPeriod}</p>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-border bg-surface-2 px-3 py-2">
        <p className="text-[10px] uppercase tracking-wide text-ink-muted">Primary catalyst</p>
        <p className="mt-0.5 text-xs text-ink-secondary">{opp.primaryCatalyst}</p>
      </div>

      <button
        onClick={() => onReview(opp.symbol)}
        className="mt-3 flex items-center justify-center gap-1.5 rounded-lg bg-brand-blue px-3 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90"
      >
        Review Opportunity <ArrowRight size={13} />
      </button>
    </div>
  );
}

export default function ConvictionOpportunities({
  onReview,
}: {
  onReview: (symbol: string) => void;
}) {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-ink-primary">
            <Target size={16} className="text-brand-blue" />
            Highest-Conviction Opportunities
          </h2>
          <p className="mt-0.5 text-xs text-ink-secondary">
            The model's top setups right now, ranked by catalyst score.
          </p>
        </div>
        <DemoBadge />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {convictionOpportunities.map((opp) => (
          <OpportunityCard key={opp.symbol} opp={opp} onReview={onReview} />
        ))}
      </div>
    </section>
  );
}
