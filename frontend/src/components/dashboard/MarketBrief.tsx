import { Newspaper } from "lucide-react";
import DemoBadge from "../DemoBadge";
import { marketBrief } from "../../mockDashboardData";

export default function MarketBrief() {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink-primary">
          <Newspaper size={16} className="text-brand-blue" />
          Today's Market Brief
        </h2>
        <DemoBadge />
      </div>
      <p className="text-sm leading-relaxed text-ink-secondary">{marketBrief}</p>
      <p className="mt-3 text-[11px] text-ink-muted">
        Summarized from market-model outputs, the economic calendar, news, technical data, and
        behavioral-analysis signals — never invented conditions.
      </p>
    </section>
  );
}
