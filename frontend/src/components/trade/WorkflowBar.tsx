import { Check } from "lucide-react";
import type { PageId } from "../../types/nav";

/**
 * The Next Steps workflow: shows where the user is in the investing process
 * (a user action, never a buy/sell recommendation). Each step links to the
 * page for that stage. Steps before the active one read as complete; steps
 * after preview what's ahead.
 */
const WORKFLOW_STEPS: { label: string; page: PageId }[] = [
  { label: "Define preferences", page: "home" },
  { label: "Scan the market", page: "markets" },
  { label: "Review opportunities", page: "trade" },
  { label: "Build strategy", page: "analysis" },
  { label: "Confirm trade", page: "ticket" },
  { label: "Monitor", page: "portfolio" },
];

export default function WorkflowBar({
  current,
  onNavigate,
}: {
  current: number;
  onNavigate: (page: PageId) => void;
}) {
  return (
    <div className="cq-glass mb-6 flex items-center gap-2 overflow-x-auto rounded-2xl p-3">
      {WORKFLOW_STEPS.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={step.label} className="flex flex-shrink-0 items-center gap-2">
            <button
              onClick={() => onNavigate(step.page)}
              aria-current={active ? "step" : undefined}
              className={`flex flex-shrink-0 items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] transition-colors ${
                active
                  ? "border border-brand-blue/40 bg-brand-blue/15 text-ink-primary"
                  : done
                    ? "text-ink-secondary hover:bg-surface-2 hover:text-ink-primary"
                    : "text-ink-muted hover:bg-surface-2 hover:text-ink-secondary"
              }`}
            >
              <span
                className={`grid h-[22px] w-[22px] place-items-center rounded-full font-mono text-[11px] font-bold ${
                  active
                    ? "bg-brand-blue text-white"
                    : done
                      ? "bg-status-good-soft text-status-good"
                      : "border border-border-strong text-ink-muted"
                }`}
              >
                {done ? <Check size={12} /> : i + 1}
              </span>
              <span className="whitespace-nowrap">{step.label}</span>
            </button>
            {i < WORKFLOW_STEPS.length - 1 && (
              <span className="flex-shrink-0 text-ink-muted">›</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
