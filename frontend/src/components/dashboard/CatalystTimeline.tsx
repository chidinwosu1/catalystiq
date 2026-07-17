import { CalendarClock } from "lucide-react";
import DemoBadge from "../DemoBadge";
import { todaysCatalysts } from "../../mockDashboardData";
import { impactRole, roleClasses } from "../../lib/theme";

export default function CatalystTimeline() {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink-primary">
          <CalendarClock size={16} className="text-brand-blue" />
          Today's Market Catalysts
        </h2>
        <DemoBadge />
      </div>

      <ul className="space-y-2.5">
        {todaysCatalysts.map((c) => {
          const impact = roleClasses[impactRole(c.impact)];
          return (
            <li
              key={c.title}
              className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex min-w-0 items-start gap-3">
                <div className="w-20 shrink-0">
                  <p className="text-xs font-semibold text-ink-primary">{c.time}</p>
                  <p className="text-[10px] uppercase tracking-wide text-ink-muted">
                    {c.category}
                  </p>
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink-primary">{c.title}</p>
                  <p className="mt-0.5 text-xs text-ink-secondary">Affects: {c.assetsAffected}</p>
                </div>
              </div>
              <span
                className={`self-start rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide sm:self-center ${impact.border} ${impact.bg} ${impact.text}`}
              >
                {c.impact} impact
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
