import { Globe, TrendingDown, TrendingUp } from "lucide-react";
import DemoBadge from "../DemoBadge";
import { marketSnapshot } from "../../mockDashboardData";

export default function MarketSnapshot() {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink-primary">
          <Globe size={16} className="text-brand-blue" />
          Market Snapshot
        </h2>
        <DemoBadge />
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {marketSnapshot.map((row) => {
          const up = row.changePct >= 0;
          return (
            <div key={row.symbol} className="rounded-lg border border-border px-3 py-2.5">
              <p className="text-xs text-ink-secondary">{row.label}</p>
              <p className="mt-0.5 text-base font-semibold text-ink-primary">{row.level}</p>
              <p
                className={`mt-0.5 flex items-center gap-1 text-xs font-medium ${
                  up ? "text-status-good" : "text-status-critical"
                }`}
              >
                {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                {up ? "+" : ""}
                {row.changePct.toFixed(1)}%
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
