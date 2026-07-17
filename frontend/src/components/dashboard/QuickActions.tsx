import { BookOpen, Briefcase, LineChart, Radar, Send } from "lucide-react";
import type { PageId } from "../../types/nav";

const ACTIONS: { label: string; icon: typeof Radar; page: PageId }[] = [
  { label: "Scan Market", icon: Radar, page: "markets" },
  { label: "Research a Stock", icon: LineChart, page: "analysis" },
  { label: "Place a Trade", icon: Send, page: "trade" },
  { label: "View Portfolio", icon: Briefcase, page: "portfolio" },
  { label: "Review Journal", icon: BookOpen, page: "analysis" },
];

export default function QuickActions({
  onNavigate,
}: {
  onNavigate: (page: PageId) => void;
}) {
  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h2 className="mb-4 text-base font-semibold text-ink-primary">Quick Actions</h2>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {ACTIONS.map((a) => {
          const Icon = a.icon;
          return (
            <button
              key={a.label}
              onClick={() => onNavigate(a.page)}
              className="group flex flex-col items-center gap-2 rounded-lg border border-border bg-surface-2/40 px-3 py-3 text-center transition-colors hover:border-brand-blue/40 hover:bg-surface-2"
            >
              <span className="rounded-lg bg-surface-2 p-2 text-brand-blue">
                <Icon size={16} />
              </span>
              <span className="text-xs font-medium text-ink-secondary group-hover:text-ink-primary">
                {a.label}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
