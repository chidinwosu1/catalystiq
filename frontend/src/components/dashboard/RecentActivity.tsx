import { BookOpen, Eye, History, LineChart, Receipt } from "lucide-react";
import { recentActivity, type ActivityKind } from "../../mockDashboardData";

const KIND_META: Record<ActivityKind, { icon: typeof Eye; verb: string }> = {
  trade: { icon: Receipt, verb: "Trade" },
  viewed: { icon: Eye, verb: "Viewed" },
  analysis: { icon: LineChart, verb: "Analysis" },
  journal: { icon: BookOpen, verb: "Journal" },
};

export default function RecentActivity({
  onResume,
}: {
  onResume: (symbol: string) => void;
}) {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h2 className="mb-4 flex items-center gap-2 text-base font-semibold text-ink-primary">
        <History size={16} className="text-brand-blue" />
        Recent Activity
      </h2>

      <ul className="space-y-1.5">
        {recentActivity.map((a, i) => {
          const Icon = KIND_META[a.kind].icon;
          return (
            <li key={`${a.symbol}-${i}`}>
              <button
                onClick={() => onResume(a.symbol)}
                className="flex w-full items-center justify-between gap-3 rounded-lg border border-transparent px-3 py-2 text-left transition-colors hover:border-border hover:bg-surface-2"
              >
                <span className="flex items-center gap-2.5">
                  <span className="rounded-md bg-surface-2 p-1.5 text-ink-secondary">
                    <Icon size={14} />
                  </span>
                  <span>
                    <span className="text-sm font-medium text-ink-primary">{a.symbol}</span>
                    <span className="ml-2 text-xs text-ink-secondary">{a.label}</span>
                  </span>
                </span>
                <span className="shrink-0 text-[11px] text-ink-muted">{a.timeAgo}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[11px] text-ink-muted">Pick up where you left off.</p>
    </section>
  );
}
