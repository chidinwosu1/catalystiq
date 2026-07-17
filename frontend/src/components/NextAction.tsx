import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";

interface NextActionProps {
  /** Where the user is in the workflow, e.g. "Step 2 of 5 · Research". */
  step?: string;
  /** Short prompt describing what to do next. */
  prompt: string;
  /** Primary next-step button label. */
  label: string;
  onClick: () => void;
  /** Optional secondary action. */
  secondary?: { label: string; onClick: () => void };
  icon?: ReactNode;
}

/**
 * The connective tissue of the guided investment workflow: every page ends
 * with a clear next action (Command Center → Research → Trade → Monitor), so
 * the app reads as one continuous decision flow rather than isolated screens.
 */
export default function NextAction({
  step,
  prompt,
  label,
  onClick,
  secondary,
  icon,
}: NextActionProps) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-brand-blue/25 bg-gradient-to-r from-brand-blue/10 to-transparent p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        {step && (
          <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-blue">
            {step}
          </p>
        )}
        <p className="mt-0.5 text-sm text-ink-secondary">{prompt}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {secondary && (
          <button
            onClick={secondary.onClick}
            className="rounded-lg border border-border px-3 py-2 text-sm font-medium text-ink-secondary hover:text-ink-primary"
          >
            {secondary.label}
          </button>
        )}
        <button
          onClick={onClick}
          className="flex items-center justify-center gap-1.5 rounded-lg bg-brand-blue px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
        >
          {icon}
          {label}
          <ArrowRight size={15} />
        </button>
      </div>
    </div>
  );
}
