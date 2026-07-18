import { Check } from "lucide-react";

/**
 * The Next Steps workflow: shows where the user is in the investing process
 * (a user action, never a buy/sell recommendation). Steps before the active
 * one read as complete; steps after preview what's ahead.
 */
const WORKFLOW_STEPS = [
  "Define preferences",
  "Scan the market",
  "Review opportunities",
  "Build strategy",
  "Confirm trade",
  "Monitor",
] as const;

export default function WorkflowBar({ current }: { current: number }) {
  return (
    <div className="cq-glass mb-6 flex items-center gap-2 overflow-x-auto rounded-2xl p-3">
      {WORKFLOW_STEPS.map((label, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={label} className="flex flex-shrink-0 items-center gap-2">
            <div
              className={`flex flex-shrink-0 items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] ${
                active
                  ? "border border-brand-blue/40 bg-brand-blue/15 text-ink-primary"
                  : done
                    ? "text-ink-secondary"
                    : "text-ink-muted"
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
              <span className="whitespace-nowrap">{label}</span>
            </div>
            {i < WORKFLOW_STEPS.length - 1 && (
              <span className="flex-shrink-0 text-ink-muted">›</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
