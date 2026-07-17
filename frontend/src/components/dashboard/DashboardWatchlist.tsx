import { Eye, Newspaper } from "lucide-react";
import DemoBadge from "../DemoBadge";
import RatingBadge from "../RatingBadge";
import { dashboardWatchlist } from "../../mockDashboardData";

export default function DashboardWatchlist({
  onViewAnalysis,
}: {
  onViewAnalysis: (symbol: string) => void;
}) {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink-primary">
          <Eye size={16} className="text-brand-blue" />
          Watchlist
        </h2>
        <DemoBadge />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
              <th className="py-2 pr-3 font-medium">Ticker</th>
              <th className="py-2 pr-3 font-medium">Price</th>
              <th className="py-2 pr-3 font-medium">Change</th>
              <th className="py-2 pr-3 font-medium">Catalyst</th>
              <th className="py-2 pr-3 font-medium">Recommendation</th>
              <th className="py-2 pr-3 font-medium">Prob. profit</th>
              <th className="py-2 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {dashboardWatchlist.map((w) => {
              const up = w.changePct >= 0;
              return (
                <tr key={w.symbol} className="border-b border-border last:border-0">
                  <td className="py-2.5 pr-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium text-ink-primary">{w.symbol}</span>
                      {w.hasNews && (
                        <span
                          title="Fresh news"
                          className="flex items-center text-brand-blue"
                          aria-label="Has fresh news"
                        >
                          <Newspaper size={12} />
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-ink-muted">{w.companyName}</p>
                  </td>
                  <td className="py-2.5 pr-3 text-ink-secondary">{w.price}</td>
                  <td
                    className={`py-2.5 pr-3 font-medium ${
                      up ? "text-status-good" : "text-status-critical"
                    }`}
                  >
                    {up ? "+" : ""}
                    {w.changePct.toFixed(1)}%
                  </td>
                  <td className="py-2.5 pr-3 font-medium text-brand-blue">{w.catalystScore}</td>
                  <td className="py-2.5 pr-3">
                    <RatingBadge rating={w.recommendation} />
                  </td>
                  <td className="py-2.5 pr-3 text-ink-secondary">{w.probabilityOfProfit}%</td>
                  <td className="py-2.5">
                    <button
                      onClick={() => onViewAnalysis(w.symbol)}
                      className="font-medium text-brand-blue hover:underline"
                    >
                      Research
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
